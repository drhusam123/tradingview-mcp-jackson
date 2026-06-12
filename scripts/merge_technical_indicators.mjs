#!/usr/bin/env node
/**
 * Merge technical_indicators_cache → indicators_cache with source='tv'.
 * Local rows (source='local') are never overwritten.
 *
 * Usage:
 *   node scripts/merge_technical_indicators.mjs
 *   node scripts/merge_technical_indicators.mjs --date 2026-06-11
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { getDB } from '../src/egx/index.js';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const dateArg = (() => {
  const i = process.argv.indexOf('--date');
  return i >= 0 ? process.argv[i + 1] : null;
})();

function ensureSourceColumn(db) {
  const cols = new Set(db.prepare('PRAGMA table_info(indicators_cache)').all().map(r => r.name));
  if (!cols.has('source')) {
    db.exec("ALTER TABLE indicators_cache ADD COLUMN source TEXT DEFAULT 'local'");
  }
}

function mergeTechnical({ date = null } = {}) {
  const db = getDB();
  ensureSourceColumn(db);

  const hasTech = db.prepare(
    "SELECT 1 ok FROM sqlite_master WHERE type='table' AND name='technical_indicators_cache'",
  ).get()?.ok === 1;
  if (!hasTech) {
    return { merged: 0, skipped: 0, error: 'no technical_indicators_cache table' };
  }

  const rows = date
    ? db.prepare('SELECT * FROM technical_indicators_cache WHERE fetch_date=?').all(date)
    : db.prepare('SELECT * FROM technical_indicators_cache').all();

  const upsert = db.prepare(`
    INSERT INTO indicators_cache (
      symbol, bar_date, ema20, ema50, ema200,
      above_ema20, above_ema50, above_ema200,
      rsi14, macd_line, macd_signal, macd_hist,
      bb_upper, bb_middle, bb_lower, bb_width, vol_ratio_20,
      source, updated_at
    ) VALUES (
      ?, ?, ?, ?, ?,
      ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      'tv', datetime('now')
    )
    ON CONFLICT(symbol, bar_date) DO UPDATE SET
      ema20         = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.ema20         ELSE COALESCE(excluded.ema20, indicators_cache.ema20) END,
      ema50         = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.ema50         ELSE COALESCE(excluded.ema50, indicators_cache.ema50) END,
      ema200        = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.ema200        ELSE COALESCE(excluded.ema200, indicators_cache.ema200) END,
      rsi14         = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.rsi14         ELSE COALESCE(excluded.rsi14, indicators_cache.rsi14) END,
      macd_line     = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.macd_line     ELSE COALESCE(excluded.macd_line, indicators_cache.macd_line) END,
      macd_signal   = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.macd_signal   ELSE COALESCE(excluded.macd_signal, indicators_cache.macd_signal) END,
      macd_hist     = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.macd_hist     ELSE COALESCE(excluded.macd_hist, indicators_cache.macd_hist) END,
      bb_upper      = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.bb_upper      ELSE COALESCE(excluded.bb_upper, indicators_cache.bb_upper) END,
      bb_middle     = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.bb_middle     ELSE COALESCE(excluded.bb_middle, indicators_cache.bb_middle) END,
      bb_lower      = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.bb_lower      ELSE COALESCE(excluded.bb_lower, indicators_cache.bb_lower) END,
      bb_width      = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.bb_width      ELSE COALESCE(excluded.bb_width, indicators_cache.bb_width) END,
      vol_ratio_20  = CASE WHEN indicators_cache.source = 'local' THEN indicators_cache.vol_ratio_20  ELSE COALESCE(excluded.vol_ratio_20, indicators_cache.vol_ratio_20) END,
      source        = CASE WHEN indicators_cache.source = 'local' THEN 'local' ELSE 'tv' END,
      updated_at    = datetime('now')
  `);

  let merged = 0;
  let skipped = 0;

  for (const t of rows) {
    const close = t.close_price ?? null;
    const ema20 = t.ema_20 ?? null;
    const ema50 = t.ema_50 ?? null;
    const ema200 = t.ema_200 ?? null;
    const above = (ema, px) => (ema != null && px != null ? (px > ema ? 1 : 0) : null);

    const existing = db.prepare(
      'SELECT source FROM indicators_cache WHERE symbol=? AND bar_date=?',
    ).get(t.symbol, t.fetch_date);
    if (existing?.source === 'local') {
      skipped++;
      continue;
    }

    upsert.run(
      t.symbol, t.fetch_date, ema20, ema50, ema200,
      above(ema20, close), above(ema50, close), above(ema200, close),
      t.rsi_14 ?? null, t.macd_value ?? null, t.macd_signal_line ?? null, t.macd_histogram ?? null,
      t.bb_upper ?? null, t.bb_middle ?? null, t.bb_lower ?? null,
      t.bb_width_pct != null ? t.bb_width_pct / 100 : null,
      t.volume_ratio ?? null,
    );
    merged++;
  }

  const bySource = db.prepare(
    "SELECT source, COUNT(*) n FROM indicators_cache GROUP BY source",
  ).all();

  return { merged, skipped, total_tech: rows.length, by_source: bySource, at: new Date().toISOString() };
}

const result = mergeTechnical({ date: dateArg });
const outPath = join(PROJECT_ROOT, 'data/merge_technical_indicators_last.json');
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(outPath, JSON.stringify(result, null, 2));

console.log('\n═══ Merge technical_indicators → indicators_cache ═══');
console.log(`  Tech rows:  ${result.total_tech}`);
console.log(`  Merged:     ${result.merged}`);
console.log(`  Skipped:    ${result.skipped} (local preserved)`);
console.log(`  By source:  ${JSON.stringify(result.by_source)}`);
console.log(`  Saved: ${outPath}\n`);
