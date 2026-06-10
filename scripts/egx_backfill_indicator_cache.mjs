#!/usr/bin/env node
/**
 * Backfill indicators_cache for historical signal dates (ULTRA outcomes + gaps).
 *
 * Usage:
 *   node scripts/egx_backfill_indicator_cache.mjs              # missing ULTRA pairs
 *   node scripts/egx_backfill_indicator_cache.mjs --date 2026-06-03  # full universe that day
 *   node scripts/egx_backfill_indicator_cache.mjs --since 2026-05-01
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import {
  getOHLCV, saveIndicatorsCache, EGX_UNIVERSE, getIndicatorsCacheStats,
} from '../src/egx/index.js';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH } from './lib/delivery_audit.mjs';
import { buildIndicatorPayload, formatBarDate } from './lib/indicator_snapshot.mjs';

loadEnv();

const BARS_LIMIT = 500;
const DATE_ARG = (() => { const i = process.argv.indexOf('--date'); return i >= 0 ? process.argv[i + 1] : null; })();
const SINCE_ARG = (() => { const i = process.argv.indexOf('--since'); return i >= 0 ? process.argv[i + 1] : null; })();
const AS_JSON = process.argv.includes('--json');

function barsForDate(symbol, targetDate) {
  const all = getOHLCV(symbol, BARS_LIMIT);
  const endTs = Math.floor(new Date(`${targetDate}T23:59:59`).getTime() / 1000);
  const through = all.filter(b => b.time <= endTs);
  if (through.length < 30) return null;
  const last = through[through.length - 1];
  if (formatBarDate(last.time) !== targetDate) return null;
  return through;
}

function backfillPair(symbol, targetDate) {
  const bars = barsForDate(symbol, targetDate);
  if (!bars) return { status: 'skip', reason: 'no_bars' };
  const payload = buildIndicatorPayload(bars);
  if (!payload) return { status: 'skip', reason: 'compute_failed' };
  saveIndicatorsCache(symbol, targetDate, payload.ind);
  return { status: 'ok', vol: payload.ind.volumeRatio20 };
}

function missingUltraPairs(db, since) {
  let sql = `
    SELECT DISTINCT ro.symbol, ro.signal_date AS bar_date
    FROM recommendation_outcomes ro
    LEFT JOIN indicators_cache ic
      ON ic.symbol = ro.symbol AND ic.bar_date = ro.signal_date
    WHERE ro.conviction_tier = 'ULTRA_CONVICTION'
      AND ic.symbol IS NULL
  `;
  const params = [];
  if (since) {
    sql += ' AND ro.signal_date >= ?';
    params.push(since);
  }
  sql += ' ORDER BY ro.signal_date DESC, ro.symbol';
  return db.prepare(sql).all(...params);
}

function run() {
  if (!existsSync(DB_PATH)) {
    console.error('No database at', DB_PATH);
    process.exit(1);
  }

  const db = new Database(DB_PATH, { readonly: true });
  const results = { ok: 0, skip: 0, err: 0, pairs: [] };

  if (DATE_ARG) {
    const targets = [...new Set(EGX_UNIVERSE)];
    console.log(`\n▶  Backfill universe for ${DATE_ARG} (${targets.length} symbols)\n`);
    for (const sym of targets) {
      try {
        const r = backfillPair(sym, DATE_ARG);
        if (r.status === 'ok') results.ok++;
        else results.skip++;
      } catch {
        results.err++;
      }
    }
  } else {
    const pairs = missingUltraPairs(db, SINCE_ARG);
    console.log(`\n▶  Backfill ${pairs.length} missing ULTRA (symbol, date) pairs\n`);
    for (const { symbol, bar_date: barDate } of pairs) {
      try {
        const r = backfillPair(symbol, barDate);
        results.pairs.push({ symbol, bar_date: barDate, ...r });
        if (r.status === 'ok') results.ok++;
        else results.skip++;
      } catch (e) {
        results.err++;
        results.pairs.push({ symbol, bar_date: barDate, status: 'err', reason: e.message?.slice(0, 60) });
      }
    }
  }
  db.close();

  const stats = getIndicatorsCacheStats();
  const report = {
    at: new Date().toISOString(),
    mode: DATE_ARG ? 'date' : 'ultra_missing',
    date: DATE_ARG,
    since: SINCE_ARG,
    ...results,
    cache: stats,
  };

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/indicator_backfill_last.json'), JSON.stringify(report, null, 2));

  if (AS_JSON) {
    console.log(JSON.stringify(report, null, 2));
    process.exit(0);
  }

  console.log(`\n✅ Backfill: ${results.ok} ok | ${results.skip} skip | ${results.err} err`);
  if (stats) console.log(`   Cache: ${stats.symbols_count} symbols | ${stats.total_rows} rows | latest ${stats.latest_date}`);
  console.log('   Saved: data/indicator_backfill_last.json\n');
}

run();
