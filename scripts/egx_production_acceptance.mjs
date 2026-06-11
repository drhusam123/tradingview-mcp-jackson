#!/usr/bin/env node
/**
 * EGX Production Acceptance Gate
 * ==============================
 * Verifies client-delivery safety before production Telegram is enabled.
 *
 * This does not prove market alpha. It proves that stale/debug/research output
 * cannot reach clients through the production delivery path.
 */

import { execFileSync } from 'child_process';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { sendTelegram, validateTelegramPayload } from '../src/egx/notify.js';
import { tradingDayStaleness, freshnessReferenceDate } from './lib/egx_calendar.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const DB_PATH = join(ROOT, 'data', 'egx_trading.db');

let pass = 0;
let fail = 0;
const rows = [];

function record(ok, name, detail = '') {
  rows.push({ ok, name, detail });
  if (ok) pass += 1;
  else fail += 1;
}

function latestOhlcv(db) {
  return db.prepare("SELECT MAX(date(bar_time, 'unixepoch')) AS latest FROM ohlcv_history").get()?.latest ?? null;
}

function crontab() {
  try {
    return execFileSync('crontab', ['-l'], { encoding: 'utf8', timeout: 5000 });
  } catch {
    return '';
  }
}

console.log('EGX Production Acceptance Gate');
console.log(new Date().toISOString());
console.log('');

try {
  if (!existsSync(DB_PATH)) throw new Error(`DB not found: ${DB_PATH}`);
  const db = new Database(DB_PATH, { readonly: true });

  const latest = latestOhlcv(db);
  record(Boolean(latest), 'OHLCV exists', latest ? `latest=${latest}` : 'no latest date');

  const badFingerprint = db.prepare(`
    SELECT COUNT(*) AS n
    FROM ohlcv_history
    WHERE ABS(open - 41.61) < 0.001
      AND ABS(high - 42.18) < 0.001
      AND ABS(low - 41.61) < 0.001
      AND ABS(close - 41.70) < 0.001
      AND ABS(volume - 83579) < 0.001
  `).get()?.n ?? 0;
  record(badFingerprint === 0, 'Reject known TradingView fallback fingerprint', `bad_rows=${badFingerprint}`);

  const finalLatest = db.prepare('SELECT MAX(trade_date) AS d FROM final_signals').get()?.d ?? null;
  const finalStats = finalLatest
    ? db.prepare('SELECT COUNT(*) AS total, SUM(actionable) AS actionable FROM final_signals WHERE trade_date=?').get(finalLatest)
    : { total: 0, actionable: 0 };
  record(Boolean(finalLatest), 'final_signals exists', finalLatest ? `latest=${finalLatest} total=${finalStats.total} actionable=${finalStats.actionable ?? 0}` : 'missing');

  const mig = db.prepare("SELECT COUNT(*) AS n FROM schema_migrations WHERE version='002'").get()?.n ?? 0;
  record(mig >= 1, 'Schema migration 002 applied', `rows=${mig}`);

  db.close();

  const debugPayload = '🧠 EGX COGNITION ENGINE\nSTOCK DNA (0 stocks)\n+51.3pp → null @ undefined';
  const debugQa = validateTelegramPayload(debugPayload);
  record(!debugQa.ok, 'Telegram QA blocks debug/research payload', debugQa.ok ? 'unexpectedly allowed' : debugQa.issues.join('; '));

  const internalSend = await sendTelegram('Internal research smoke test', { reportDate: latest });
  record(
    internalSend.policyBlocked === true,
    'Internal scripts cannot send Telegram by default',
    internalSend.error || 'unexpectedly allowed'
  );

  const clientPayload = [
    '📊 <b>نظام EGX الذكي</b>',
    '⏸ <b>لا فرص تنفيذية اليوم</b> — لا توجد إشارة نهائية مؤكدة لنفس التاريخ',
    '<i>للمعلومات فقط • ليس توصية استثمارية</i>',
  ].join('\n');
  const cal = latest ? tradingDayStaleness(latest, freshnessReferenceDate()) : null;
  const staleSessions = cal ? Number(cal.staleness_trading_days ?? 0) : 0;
  const clientQa = validateTelegramPayload(clientPayload, {
    clientDelivery: true,
    reportDate: latest || freshnessReferenceDate(),
    finalActionableCount: Number(finalStats.actionable ?? 0),
  });
  const staleExpected = staleSessions > 0;
  record(
    staleExpected ? !clientQa.ok : clientQa.ok,
    'Telegram QA freshness gate (trading sessions)',
    staleExpected
      ? `expected block: stale=${staleSessions} latest=${latest}`
      : `expected allow: latest=${latest} market=${cal?.market_status ?? 'n/a'}`
  );

  const cron = crontab();
  const notifyLines = cron.split('\n').filter(l => l.includes('--notify'));
  record(notifyLines.length === 0, 'Cron has no research --notify jobs', notifyLines.length ? notifyLines.join(' | ') : 'clean');

  const clientDeliveryOwners = cron.split('\n').filter(l =>
    /egx_telegram_cron\.mjs|egx_telegram_daily\.mjs|EGX-TELEGRAM/.test(l),
  );
  const usesCronWrapper = /egx_telegram_cron\.mjs/.test(cron);
  record(usesCronWrapper, 'Telegram uses egx_telegram_cron wrapper', usesCronWrapper ? 'ok' : 'direct daily — missing prepare-send');
  record(clientDeliveryOwners.length <= 2, 'Limited Telegram cron owners', `${clientDeliveryOwners.length} line(s)`);

  let cardsOk = false;
  let cardsDetail = '';
  const hasActionable = Number(finalStats.actionable ?? 0) > 0;
  const cardDate = finalLatest || latest;
  try {
    const args = hasActionable
      ? ['scripts/python/telegram_send_cards.py', cardDate, '--dry-run']
      : ['scripts/python/telegram_send_cards.py', cardDate];
    const py = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
    const out = execFileSync(py, args, {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 60000,
      env: {
        ...process.env,
        TELEGRAM_BOT_TOKEN: '',
        TELEGRAM_CHAT_ID: '',
      },
    });
    cardsDetail = out.slice(-500);
    if (hasActionable) {
      cardsOk = /DRY RUN|Would send|Generated|Generating cards/.test(out)
        && !/TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set/.test(out);
    } else {
      cardsOk = /No same-date final_signals actionable=1|no_actionable_guard|CLIENT QA BLOCKED/.test(out);
    }
  } catch (err) {
    const combined = `${err.stdout ?? ''}\n${err.stderr ?? ''}`;
    cardsOk = !hasActionable && /CLIENT QA BLOCKED visual cards|visual client cards require|trusted OHLCV is stale|no_actionable_guard/.test(combined);
    cardsDetail = combined.slice(-500);
  }
  record(
    cardsOk,
    hasActionable
      ? 'Visual cards acceptance uses dry-run for same-date actionable signals'
      : 'Visual cards cannot send without same-date actionable signal',
    cardsDetail.trim()
  );
} catch (err) {
  record(false, 'Acceptance runner error', err.message);
}

for (const row of rows) {
  console.log(`${row.ok ? 'PASS' : 'FAIL'} ${row.name}${row.detail ? ` — ${row.detail}` : ''}`);
}

console.log('');
console.log(`Summary: ${pass} PASS / ${fail} FAIL`);
process.exit(fail === 0 ? 0 : 1);
