/**
 * Bridge: notification_delivery_audit → recommendation_outcomes.client_delivered
 * Ensures P6 proof can count only client-sent signals.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';
import { evaluateSignalAtDate } from './egx_safety_check.mjs';

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

/** Seed recommendation_outcomes rows for successfully delivered signals. */
export function seedDeliveredOutcomes({ lookbackDays = 120 } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB' };

  ensureDeliveredColumn();
  const d = db();
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);
  const cutoffIso = cutoff.toISOString().slice(0, 10);

  const pairs = d.prepare(`
    SELECT DISTINCT signal_date, symbol
    FROM notification_delivery_audit
    WHERE send_success = 1 AND dry_run = 0 AND deliverable = 1
      AND symbol IS NOT NULL AND signal_date >= ?
  `).all(cutoffIso);

  const insert = d.prepare(`
    INSERT OR IGNORE INTO recommendation_outcomes
      (signal_date, report_date, symbol, conviction_tier,
       entry_price, stop_loss, t1_target, ues, ml_score,
       behavioral_class, quality_gate_passed, outcome_filled, client_delivered)
    SELECT us.signal_date, us.signal_date, us.symbol, us.conviction_tier,
           us.entry_price, us.stop_loss, us.t1_target,
           us.unified_score, us.explosion_score, us.behavioral_class,
           COALESCE(us.quality_gate_passed, 0), 0, 1
    FROM unified_signals us
    WHERE us.signal_date = ? AND us.symbol = ?
      AND us.conviction_tier NOT IN ('REJECT', 'WATCH')
    LIMIT 1
  `);

  let seeded = 0;
  for (const row of pairs) {
    seeded += insert.run(row.signal_date, row.symbol).changes;
  }
  d.close();
  return { ok: true, pairs: pairs.length, seeded };
}

/** Mark outcomes delivered when Telegram send succeeded. */
export function syncDeliveredOutcomes({ lookbackDays = 120 } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB' };

  const seed = seedDeliveredOutcomes({ lookbackDays });

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
      SUM(CASE WHEN client_delivered = 1 AND outcome_filled >= 5 THEN 1 ELSE 0 END) AS delivered_filled_n,
      COUNT(*) AS total_n
    FROM recommendation_outcomes
  `).get();

  d.close();

  return {
    ok: true,
    pairs_synced: delivered.length,
    rows_updated: marked,
    seeded: seed.seeded ?? 0,
    delivered_total: stats?.delivered_n ?? 0,
    delivered_filled: stats?.delivered_filled_n ?? 0,
    ultra_delivered: stats?.delivered_n ?? 0,
    ultra_total_filled: stats?.total_n ?? 0,
  };
}

/** Backfill recommendation_outcomes.quality_gate_passed from live safety rules. */
export function backfillOutcomeSafetyGate({ tier = 'ULTRA_CONVICTION' } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB' };

  ensureDeliveredColumn();
  const d = db();
  const cols = new Set(d.prepare('PRAGMA table_info(recommendation_outcomes)').all().map(r => r.name));
  if (!cols.has('quality_gate_passed')) {
    try {
      d.exec('ALTER TABLE recommendation_outcomes ADD COLUMN quality_gate_passed INTEGER DEFAULT 0');
    } catch { /* */ }
  }

  const rows = d.prepare(`
    SELECT id, symbol, signal_date
    FROM recommendation_outcomes
    WHERE conviction_tier = ? AND outcome_filled >= 5
  `).all(tier);

  const upd = d.prepare(`
    UPDATE recommendation_outcomes
    SET quality_gate_passed = ?
    WHERE id = ?
  `);

  let passed = 0;
  let blocked = 0;
  for (const row of rows) {
    const ev = evaluateSignalAtDate(row.symbol, row.signal_date, {
      historical: true,
      counterfactual: true,
    });
    const ok = ev.decision !== 'BLOCKED' ? 1 : 0;
    upd.run(ok, row.id);
    if (ok) passed += 1;
    else blocked += 1;
  }

  d.close();
  return { ok: true, tier, total: rows.length, passed, blocked };
}
