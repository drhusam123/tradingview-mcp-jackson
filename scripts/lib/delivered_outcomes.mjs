/**
 * Bridge: notification_delivery_audit → recommendation_outcomes.client_delivered
 * Ensures P6 proof can count only client-sent signals.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';

function db() {
  const d = new Database(DB_PATH);
  d.pragma('journal_mode = WAL');
  d.pragma('busy_timeout = 10000');
  return d;
}

export function ensureDeliveredColumn() {
  const d = db();
  const cols = new Set(d.prepare('PRAGMA table_info(recommendation_outcomes)').all().map(r => r.name));
  if (!cols.has('client_delivered')) {
    try {
      d.exec('ALTER TABLE recommendation_outcomes ADD COLUMN client_delivered INTEGER DEFAULT 0');
    } catch { /* */ }
  }
  if (!cols.has('delivered_at')) {
    try {
      d.exec('ALTER TABLE recommendation_outcomes ADD COLUMN delivered_at TEXT');
    } catch { /* */ }
  }
  d.close();
}

/** Mark outcomes delivered when Telegram send succeeded. */
export function syncDeliveredOutcomes({ lookbackDays = 120 } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB' };

  ensureDeliveredColumn();
  const d = db();
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);
  const cutoffIso = cutoff.toISOString().slice(0, 10);

  const delivered = d.prepare(`
    SELECT DISTINCT signal_date, symbol, MAX(created_at) AS sent_at
    FROM notification_delivery_audit
    WHERE send_success = 1
      AND dry_run = 0
      AND deliverable = 1
      AND symbol IS NOT NULL
      AND signal_date >= ?
    GROUP BY signal_date, symbol
  `).all(cutoffIso);

  const upd = d.prepare(`
    UPDATE recommendation_outcomes
    SET client_delivered = 1, delivered_at = COALESCE(delivered_at, ?)
    WHERE signal_date = ? AND symbol = ?
  `);

  let marked = 0;
  for (const row of delivered) {
    const info = upd.run(row.sent_at, row.signal_date, row.symbol);
    marked += info.changes;
  }

  const stats = d.prepare(`
    SELECT
      SUM(CASE WHEN client_delivered = 1 THEN 1 ELSE 0 END) AS delivered_n,
      COUNT(*) AS total_n
    FROM recommendation_outcomes
    WHERE conviction_tier = 'ULTRA_CONVICTION' AND outcome_filled >= 5
  `).get();

  d.close();

  return {
    ok: true,
    pairs_synced: delivered.length,
    rows_updated: marked,
    ultra_delivered: stats?.delivered_n ?? 0,
    ultra_total_filled: stats?.total_n ?? 0,
  };
}
