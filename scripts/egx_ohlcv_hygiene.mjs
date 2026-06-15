#!/usr/bin/env node
/**
 * OHLCV hygiene runner — suspicious tail purge + chronic failure archive report.
 *
 * Usage:
 *   node scripts/egx_ohlcv_hygiene.mjs --purge-symbols ROTO,UEFM
 *   node scripts/egx_ohlcv_hygiene.mjs --archive-chronic [--dry-run]
 */
import { getSymbolsLaggingOhlcv, getDB } from '../src/egx/index.js';
import {
  purgeSuspiciousTailsForSymbols,
  archiveChronicFetchFailures,
  writeHygieneReport,
  findSuspiciousTailPurgeDate,
} from './lib/ohlcv_hygiene.mjs';
import { loadEnv } from './lib/load_env.mjs';
import { tradingDayStaleness, freshnessReferenceDate } from './lib/egx_calendar.mjs';

loadEnv();

const DRY_RUN = process.argv.includes('--dry-run');
const ARCHIVE = process.argv.includes('--archive-chronic');

const symbolsArg = (() => {
  const i = process.argv.indexOf('--purge-symbols');
  return i >= 0 ? process.argv[i + 1] : null;
})();

function targetDate() {
  const db = getDB();
  const latest = db.prepare("SELECT MAX(date(bar_time,'unixepoch')) AS d FROM ohlcv_history").get()?.d;
  if (!latest) return freshnessReferenceDate();
  const cal = tradingDayStaleness(latest, freshnessReferenceDate());
  return cal.last_trading_day || latest;
}

console.log('\n═══ EGX OHLCV Hygiene ═══\n');

let symbols = [];
if (symbolsArg) {
  symbols = symbolsArg.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
} else {
  const td = targetDate();
  symbols = getSymbolsLaggingOhlcv(td).map(r => r.symbol);
}

const scan = symbols.map(sym => ({
  symbol: sym,
  purge_from: findSuspiciousTailPurgeDate(sym),
}));

const purged = DRY_RUN
  ? scan.filter(s => s.purge_from).map(s => ({ symbol: s.symbol, purged: true, fromDate: s.purge_from, dry_run: true }))
  : purgeSuspiciousTailsForSymbols(symbols.filter(s => scan.find(x => x.symbol === s && x.purge_from)));

let archive = null;
if (ARCHIVE) {
  archive = archiveChronicFetchFailures({ dryRun: DRY_RUN });
  console.log(`Chronic archive (≥${archive.threshold} fails): ${archive.archived.length} archived, ${archive.skipped.length} protected`);
  for (const a of archive.archived.slice(0, 10)) {
    console.log(`  📦 ${a.symbol} (${a.fail_count} fails)`);
  }
}

console.log(`Suspicious tail scan: ${scan.filter(s => s.purge_from).length}/${symbols.length}`);
for (const p of purged.slice(0, 10)) {
  const tag = p.dry_run ? '(dry)' : `(ohlcv -${p.ohlcv_deleted ?? 0}, ind -${p.indicators_deleted ?? 0})`;
  console.log(`  🧹 ${p.symbol} from ${p.fromDate} ${tag}`);
}

const report = { purged, archive, scanned: scan.length, mode: DRY_RUN ? 'dry-run' : 'live' };
const path = writeHygieneReport(report);
console.log(`\nReport: ${path}\n`);
