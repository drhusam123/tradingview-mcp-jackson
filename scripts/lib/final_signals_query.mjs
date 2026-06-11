/**
 * final_signals queries — exclude test/fixture dates (2099-*).
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';

export const FINAL_SIGNALS_DATE_WHERE = "trade_date NOT LIKE '2099-%'";

export function latestFinalSignalDate(db) {
  return db.prepare(
    `SELECT MAX(trade_date) AS d FROM final_signals WHERE ${FINAL_SIGNALS_DATE_WHERE}`,
  ).get()?.d ?? null;
}

export function latestActionableSignalDate(db) {
  return db.prepare(
    `SELECT MAX(trade_date) AS d FROM final_signals
     WHERE actionable=1 AND ${FINAL_SIGNALS_DATE_WHERE}`,
  ).get()?.d ?? null;
}

export const FINAL_SIGNALS_MAX_DATE_SUBQUERY =
  `(SELECT MAX(trade_date) FROM final_signals WHERE ${FINAL_SIGNALS_DATE_WHERE})`;

export function finalActionableCountForDate(reportDate, dbPath = DB_PATH) {
  if (!existsSync(dbPath) || !reportDate) return 0;
  if (String(reportDate).startsWith('2099-')) return 0;
  const db = new Database(dbPath, { readonly: true });
  try {
    const row = db.prepare(
      `SELECT COUNT(*) AS n FROM final_signals
       WHERE trade_date = ? AND actionable = 1 AND veto_reason IS NULL
         AND ${FINAL_SIGNALS_DATE_WHERE}`,
    ).get(reportDate);
    return Number(row?.n || 0);
  } finally {
    db.close();
  }
}

export function purgeTestFinalSignals(dbPath = DB_PATH) {
  if (!existsSync(dbPath)) return { deleted: 0, error: 'NO_DB' };
  const db = new Database(dbPath);
  try {
    const before = db.prepare(
      `SELECT COUNT(*) AS n FROM final_signals WHERE trade_date LIKE '2099-%'`,
    ).get()?.n ?? 0;
    if (before > 0) {
      db.prepare(`DELETE FROM final_signals WHERE trade_date LIKE '2099-%'`).run();
    }
    return { deleted: before, latest: latestFinalSignalDate(db) };
  } finally {
    db.close();
  }
}
