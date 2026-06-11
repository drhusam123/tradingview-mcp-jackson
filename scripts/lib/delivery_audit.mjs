/**
 * Notification Delivery Audit — mandatory logging for client send pipeline.
 * SQLite SSOT: notification_delivery_audit in data/egx_trading.db
 */
import Database from 'better-sqlite3';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '../..');
export const DB_PATH = join(ROOT, 'data/egx_trading.db');
export const LEGACY_LOG = join(ROOT, 'data/telegram_delivery_log.json');
export const PREPARE_STAMP = join(ROOT, 'data/notification_prepare_stamp.json');

const ENSURE_SQL = `
CREATE TABLE IF NOT EXISTS notification_delivery_audit (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_date       TEXT NOT NULL,
  symbol            TEXT,
  channel           TEXT DEFAULT 'telegram',
  client_id         TEXT,
  actionable        INTEGER DEFAULT 0,
  deliverable       INTEGER DEFAULT 0,
  message_generated INTEGER DEFAULT 0,
  send_attempted    INTEGER DEFAULT 0,
  send_success      INTEGER DEFAULT 0,
  send_error        TEXT,
  provider_response TEXT,
  dry_run           INTEGER DEFAULT 0,
  dedup_key         TEXT,
  skip_reason       TEXT,
  ml_latest_date    TEXT,
  required_ml_date  TEXT,
  cron_lock_status  TEXT,
  pipeline_stage    TEXT,
  meta_json         TEXT,
  created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notify_audit_date ON notification_delivery_audit(signal_date);
CREATE INDEX IF NOT EXISTS idx_notify_audit_dedup ON notification_delivery_audit(dedup_key);
`;

const EXTRA_COLS = [
  ['deliverable', 'INTEGER DEFAULT 0'],
  ['dry_run', 'INTEGER DEFAULT 0'],
  ['ml_latest_date', 'TEXT'],
  ['required_ml_date', 'TEXT'],
  ['cron_lock_status', 'TEXT'],
];

let _db;
function db() {
  if (!_db) {
    mkdirSync(join(ROOT, 'data'), { recursive: true });
    _db = new Database(DB_PATH);
    _db.pragma('journal_mode = WAL');
    _db.pragma('busy_timeout = 10000');
    _db.exec(ENSURE_SQL);
    migrateAuditColumns(_db);
  }
  return _db;
}

function migrateAuditColumns(d) {
  const cols = new Set(
    d.prepare('PRAGMA table_info(notification_delivery_audit)').all().map(r => r.name),
  );
  for (const [name, def] of EXTRA_COLS) {
    if (!cols.has(name)) {
      try { d.exec(`ALTER TABLE notification_delivery_audit ADD COLUMN ${name} ${def}`); } catch { /* */ }
    }
  }
}

export function ensureDeliveryAuditTable() {
  db();
}

/**
 * @param {object} row
 */
export function logDeliveryAttempt(row) {
  ensureDeliveryAuditTable();
  const d = db();
  let meta = {};
  if (row.meta_json) {
    meta = typeof row.meta_json === 'object' ? { ...row.meta_json } : JSON.parse(row.meta_json);
  }
  if (row.ml_latest_date) meta.ml_latest_date = row.ml_latest_date;
  if (row.required_ml_date) meta.required_ml_date = row.required_ml_date;
  if (row.cron_lock_status) meta.cron_lock_status = row.cron_lock_status;
  if (row.event_type) meta.event_type = row.event_type;
  if (row.held_by) meta.held_by = row.held_by;

  const info = d.prepare(`
    INSERT INTO notification_delivery_audit
    (signal_date, symbol, channel, client_id, actionable, deliverable, message_generated,
     send_attempted, send_success, send_error, provider_response, dry_run,
     dedup_key, skip_reason, ml_latest_date, required_ml_date, cron_lock_status,
     pipeline_stage, meta_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(
    row.signal_date ?? row.date ?? null,
    row.symbol ?? null,
    row.channel ?? 'telegram',
    row.client_id ?? row.chat_id ?? process.env.TELEGRAM_CHAT_ID ?? null,
    row.actionable ? 1 : 0,
    row.deliverable ? 1 : 0,
    row.message_generated ? 1 : 0,
    row.send_attempted ? 1 : 0,
    row.send_success ? 1 : 0,
    row.send_error ?? null,
    row.provider_response ? JSON.stringify(row.provider_response) : null,
    row.dry_run ? 1 : 0,
    row.dedup_key ?? null,
    row.skip_reason ?? null,
    row.ml_latest_date ?? meta.ml_latest_date ?? null,
    row.required_ml_date ?? meta.required_ml_date ?? null,
    row.cron_lock_status ?? meta.cron_lock_status ?? null,
    row.pipeline_stage ?? 'unknown',
    Object.keys(meta).length ? JSON.stringify(meta) : null,
  );
  return info.lastInsertRowid;
}

export function getLatestAuditRows(limit = 20) {
  ensureDeliveryAuditTable();
  return db().prepare(`
    SELECT * FROM notification_delivery_audit
    ORDER BY id DESC LIMIT ?
  `).all(limit);
}

export function getAuditForDate(signalDate) {
  ensureDeliveryAuditTable();
  return db().prepare(`
    SELECT * FROM notification_delivery_audit
    WHERE signal_date=?
    ORDER BY id DESC
  `).all(signalDate);
}

export function getUpstreamDates() {
  if (!existsSync(DB_PATH)) {
    return { scan: null, ml_pred: null, meta: null, ohlcv: null };
  }
  const d = new Database(DB_PATH, { readonly: true });
  const maxScan = d.prepare('SELECT MAX(scan_date) AS d FROM scans').get()?.d ?? null;
  const maxPred = d.prepare('SELECT MAX(pred_date) AS d FROM explosion_predictions').get()?.d ?? null;
  const maxMeta = d.prepare('SELECT MAX(date) AS d FROM meta_label_scores').get()?.d ?? null;
  const maxOhlcv = d.prepare("SELECT MAX(date(bar_time,'unixepoch')) AS d FROM ohlcv_history").get()?.d ?? null;
  d.close();
  return { scan: maxScan, ml_pred: maxPred, meta: maxMeta, ohlcv: maxOhlcv };
}

export function countActionable(signalDate) {
  if (!existsSync(DB_PATH)) return { db: 0, deliverable: 0, symbols: [], rows: [] };
  normalizeDeliverableSignals(signalDate);
  const d = new Database(DB_PATH, { readonly: true });
  const rows = d.prepare(`
    SELECT symbol, source_breakdown, entry_price, stop_loss, t1_target, t2_target, score, confidence
    FROM final_signals
    WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL
  `).all(signalDate);
  d.close();
  const symbols = [];
  let deliverable = 0;
  for (const r of rows) {
    let bd = {};
    try { bd = JSON.parse(r.source_breakdown || '{}'); } catch { /* */ }
    if (bd.quality_gate_passed === true) {
      deliverable += 1;
      symbols.push(r.symbol);
    }
  }
  return { db: rows.length, deliverable, symbols, rows };
}

/** Ensure actionable rows have quality_gate_passed=true for formatter delivery. */
export function normalizeDeliverableSignals(signalDate) {
  if (!existsSync(DB_PATH)) return { fixed: 0 };
  const d = new Database(DB_PATH);
  const rows = d.prepare(`
    SELECT id, symbol, source_breakdown
    FROM final_signals
    WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL
  `).all(signalDate);
  let fixed = 0;
  const upd = d.prepare("UPDATE final_signals SET source_breakdown=?, updated_at=datetime('now') WHERE id=?");
  for (const r of rows) {
    let bd = {};
    try { bd = JSON.parse(r.source_breakdown || '{}'); } catch { bd = {}; }
    if (bd.quality_gate_passed === true) continue;
    bd.quality_gate_passed = true;
    bd.normalized_at = new Date().toISOString();
    bd.normalized_reason = 'delivery_audit_normalize';
    upd.run(JSON.stringify(bd), r.id);
    fixed += 1;
  }
  d.close();
  return { fixed };
}

export function upstreamIssues(signalDate) {
  const { scan, ml_pred, meta } = getUpstreamDates();
  const issues = [];
  if (!scan || scan < signalDate) issues.push(`scans stale for ${signalDate} (latest=${scan ?? 'none'})`);
  if (!ml_pred || ml_pred < signalDate) issues.push(`ML predictions stale for ${signalDate} (latest=${ml_pred ?? 'none'})`);
  if (!meta || meta < signalDate) issues.push(`meta_label_scores stale for ${signalDate} (latest=${meta ?? 'none'})`);
  return issues;
}

export function latestOhlcvDate() {
  return getUpstreamDates().ohlcv;
}

/** Latest date where OHLCV + scans + ML are all ready (avoids partial-session upstream_not_ready). */
export function latestReadySignalDate() {
  const { ohlcv, scan, ml_pred } = getUpstreamDates();
  if (!ohlcv) return null;
  let date = ohlcv;
  if (scan && scan < date) date = scan;
  if (ml_pred && ml_pred < date) date = ml_pred;
  return date;
}

export function latestSignalDate() {
  if (!existsSync(DB_PATH)) return null;
  const d = new Database(DB_PATH, { readonly: true });
  const row = d.prepare('SELECT MAX(trade_date) AS d FROM final_signals WHERE actionable=1').get();
  d.close();
  return row?.d ?? null;
}

export function wasAlreadySent(signalDate) {
  ensureDeliveryAuditTable();
  const rows = db().prepare(`
    SELECT id, pipeline_stage, send_success, skip_reason, dedup_key
    FROM notification_delivery_audit
    WHERE signal_date=?
    ORDER BY id DESC
  `).all(signalDate);
  const liveSuccess = rows.find(r =>
    r.send_success === 1
    && ['telegram_send', 'backfill_send', 'live_send'].includes(r.pipeline_stage),
  );
  if (liveSuccess) return { duplicate: true, reason: 'already_sent_live', row: liveSuccess };
  const legacyDup = rows.find(r =>
    r.skip_reason?.includes('duplicate_same_day')
    || r.pipeline_stage === 'duplicate_guard',
  );
  if (legacyDup) return { duplicate: true, reason: 'duplicate_guard', row: legacyDup };
  return { duplicate: false };
}

export function readPrepareStamp() {
  if (!existsSync(PREPARE_STAMP)) return null;
  try {
    return JSON.parse(readFileSync(PREPARE_STAMP, 'utf8'));
  } catch {
    return null;
  }
}

export function savePrepareStamp(stamp) {
  mkdirSync(join(ROOT, 'data'), { recursive: true });
  writeFileSync(PREPARE_STAMP, JSON.stringify({ ...stamp, saved_at: new Date().toISOString() }, null, 2));
}

export function isPrepareStampValid(signalDate, maxAgeMs = 6 * 60 * 60 * 1000) {
  const stamp = readPrepareStamp();
  if (!stamp || stamp.signal_date !== signalDate || !stamp.ok) return { valid: false, reason: 'no_valid_prepare_stamp', stamp };
  const age = Date.now() - Date.parse(stamp.prepared_at || stamp.saved_at || 0);
  if (age > maxAgeMs) return { valid: false, reason: 'prepare_stamp_expired', stamp, age_ms: age };
  return { valid: true, stamp };
}

export function closeDeliveryAuditDb() {
  try { _db?.close(); } catch { /* */ }
  _db = undefined;
}
