/**
 * OHLCV hygiene — suspicious tail purge + chronic fetch-failure tracking.
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { getDB } from '../../src/egx/index.js';
import { ensureHygieneColumns } from './universe_hygiene.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const DEFAULT_FAIL_THRESHOLD = Number(process.env.EGX_OHLCV_FAIL_ARCHIVE_THRESHOLD || 5);
const JUMP_THRESHOLD = Number(process.env.EGX_OHLCV_SUSPICIOUS_JUMP_PCT || 0.5);

export function ensureOhlcvHygieneTables(db = getDB()) {
  ensureHygieneColumns(db);
  db.exec(`
    CREATE TABLE IF NOT EXISTS ohlcv_fetch_failures (
      symbol         TEXT PRIMARY KEY,
      fail_count     INTEGER NOT NULL DEFAULT 0,
      last_fail_at   TEXT,
      last_success_at TEXT,
      last_reason    TEXT
    );
  `);
  return db;
}

/**
 * Detect first bar date (inclusive) to purge when tail shows >50% close jump or SUSPICIOUS CA.
 * @returns {string|null} YYYY-MM-DD purge-from date
 */
export function findSuspiciousTailPurgeDate(symbol, db = getDB()) {
  ensureOhlcvHygieneTables(db);

  const bars = db.prepare(`
    SELECT date(bar_time, 'unixepoch') AS d, close
    FROM ohlcv_history
    WHERE symbol = ?
    ORDER BY bar_time ASC
  `).all(symbol);

  if (bars.length >= 2) {
    for (let i = 1; i < bars.length; i++) {
      const prev = Number(bars[i - 1].close);
      const curr = Number(bars[i].close);
      if (!prev || !curr) continue;
      const pct = Math.abs(curr - prev) / prev;
      if (pct > JUMP_THRESHOLD) return bars[i].d;
    }
  }

  const suspicious = db.prepare(`
    SELECT event_date FROM corporate_actions
    WHERE symbol = ? AND event_type = 'SUSPICIOUS'
    ORDER BY event_date DESC LIMIT 1
  `).get(symbol);

  return suspicious?.event_date ?? null;
}

export function purgeOhlcvFromDate(symbol, fromDate, db = getDB()) {
  ensureOhlcvHygieneTables(db);
  const ohlcvDel = db.prepare(`
    DELETE FROM ohlcv_history
    WHERE symbol = ? AND date(bar_time, 'unixepoch') >= ?
  `).run(symbol, fromDate).changes;
  const indDel = db.prepare(`
    DELETE FROM indicators_cache
    WHERE symbol = ? AND bar_date >= ?
  `).run(symbol, fromDate).changes;
  return { symbol, fromDate, ohlcv_deleted: ohlcvDel, indicators_deleted: indDel };
}

export function purgeSuspiciousTailIfNeeded(symbol, db = getDB()) {
  const fromDate = findSuspiciousTailPurgeDate(symbol, db);
  if (!fromDate) return { symbol, purged: false };
  const result = purgeOhlcvFromDate(symbol, fromDate, db);
  return { symbol, purged: true, ...result };
}

export function protectedActionableSymbols(db = getDB(), lookbackDays = 14) {
  return new Set(
    db.prepare(`
      SELECT DISTINCT symbol FROM final_signals
      WHERE actionable = 1
        AND trade_date >= date('now', ?)
        AND trade_date NOT LIKE '2099-%'
    `).all(`-${lookbackDays} days`).map(r => r.symbol),
  );
}

export function recordFetchOutcome(symbol, { success, reason = '' } = {}, db = getDB()) {
  ensureOhlcvHygieneTables(db);
  if (success) {
    db.prepare(`
      INSERT INTO ohlcv_fetch_failures(symbol, fail_count, last_success_at, last_reason)
      VALUES (?, 0, datetime('now'), NULL)
      ON CONFLICT(symbol) DO UPDATE SET
        fail_count = 0,
        last_success_at = datetime('now'),
        last_reason = NULL
    `).run(symbol);
    return { symbol, fail_count: 0 };
  }

  db.prepare(`
    INSERT INTO ohlcv_fetch_failures(symbol, fail_count, last_fail_at, last_reason)
    VALUES (?, 1, datetime('now'), ?)
    ON CONFLICT(symbol) DO UPDATE SET
      fail_count = fail_count + 1,
      last_fail_at = datetime('now'),
      last_reason = excluded.last_reason
  `).run(symbol, reason.slice(0, 200));

  const row = db.prepare('SELECT fail_count FROM ohlcv_fetch_failures WHERE symbol=?').get(symbol);
  return { symbol, fail_count: row?.fail_count ?? 1 };
}

export function archiveChronicFetchFailures({
  threshold = DEFAULT_FAIL_THRESHOLD,
  dryRun = false,
  db = getDB(),
} = {}) {
  ensureOhlcvHygieneTables(db);
  const protectedSyms = protectedActionableSymbols(db);

  const chronic = db.prepare(`
    SELECT f.symbol, f.fail_count, f.last_reason
    FROM ohlcv_fetch_failures f
    INNER JOIN stock_universe u ON u.symbol = f.symbol
    WHERE f.fail_count >= ?
      AND u.status IN ('active', 'fetched', 'pending')
    ORDER BY f.fail_count DESC, f.symbol ASC
  `).all(threshold);

  const archived = [];
  const skipped = [];

  for (const row of chronic) {
    if (protectedSyms.has(row.symbol)) {
      skipped.push({ symbol: row.symbol, reason: 'protected_actionable' });
      continue;
    }
    if (!dryRun) {
      db.prepare(`
        UPDATE stock_universe
        SET status = 'archived',
            archived_at = datetime('now'),
            hygiene_reason = ?
        WHERE symbol = ?
      `).run(`ohlcv_fetch_fail_${row.fail_count}:${(row.last_reason || 'unknown').slice(0, 80)}`, row.symbol);
    }
    archived.push({ symbol: row.symbol, fail_count: row.fail_count });
  }

  return { threshold, archived, skipped, protected_count: protectedSyms.size };
}

export function purgeSuspiciousTailsForSymbols(symbols, db = getDB()) {
  const purged = [];
  for (const symbol of symbols) {
    const r = purgeSuspiciousTailIfNeeded(symbol, db);
    if (r.purged) purged.push(r);
  }
  return purged;
}

export function writeHygieneReport(payload) {
  mkdirSync(join(ROOT, 'data'), { recursive: true });
  const path = join(ROOT, 'data/ohlcv_hygiene_last.json');
  writeFileSync(path, JSON.stringify({ at: new Date().toISOString(), ...payload }, null, 2));
  return path;
}
