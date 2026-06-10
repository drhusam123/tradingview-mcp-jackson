#!/usr/bin/env node
/**
 * Production Telegram cron — full automated client delivery.
 * prepare-send → live telegram → reconcile → audit on failure.
 *
 * Cron (via install_cron.mjs):
 *   20 15 * * 0-4  →  egx-telegram lock → this script
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import {
  latestOhlcvDate, wasAlreadySent, logDeliveryAttempt,
} from './lib/delivery_audit.mjs';
import { alertNotification, opsSuccessAlert } from './lib/notification_alert.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { isTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const NODE = process.execPath;
const DRY_RUN = process.argv.includes('--dry-run');

loadEnv();

const FORCE = process.argv.includes('--force');
const cairoToday = cairoDateParts().date;
try {
  const cal = isTradingDay(cairoToday);
  if (!cal.is_trading_day && !FORCE && !DRY_RUN) {
    console.log(`⏭  Telegram cron skip: not EGX trading day (${cal.holiday_name || 'weekend'})`);
    logDeliveryAttempt({
      signal_date: cairoToday,
      skip_reason: `CRON_NON_TRADING_DAY:${cal.holiday_name || 'weekend'}`,
      pipeline_stage: 'cron_skip',
      dedup_key: `cron:holiday:${cairoToday}`,
    });
    process.exit(0);
  }
} catch (e) {
  console.log(`⚠️  Trading day check failed: ${e.message} — continuing`);
}

const signalDate = latestOhlcvDate() || new Date().toISOString().slice(0, 10);

function run(cmd, label, { optional = false } = {}) {
  console.log(`\n▶  ${label}`);
  try {
    execSync(cmd, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 3_600_000,
      env: { ...process.env },
    });
    return true;
  } catch (e) {
    console.error(`❌  ${label}: ${e.message}`);
    if (!optional) throw e;
    return false;
  }
}

console.log('\n═══ EGX Telegram Cron (automated) ═══');
console.log(`Date: ${signalDate} | Mode: ${DRY_RUN ? 'DRY-RUN' : 'LIVE'}`);
console.log(`PYTHON_BIN: ${process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3'}`);

const sent = wasAlreadySent(signalDate);
if (sent.duplicate && !DRY_RUN) {
  console.log(`✅ Already delivered (${sent.reason}) — cron exit 0`);
  logDeliveryAttempt({
    signal_date: signalDate,
    skip_reason: `CRON_ALREADY_SENT:${sent.reason}`,
    pipeline_stage: 'cron_skip',
    dedup_key: `cron:${signalDate}`,
    meta_json: { sent },
  });
  process.exit(0);
}

try {
  if (!DRY_RUN) {
    run(`"${NODE}" scripts/egx_prod_prepare_send.mjs --date ${signalDate}`, 'Prepare send (incl. safety check)');
    run(`"${NODE}" scripts/egx_telegram_daily.mjs`, 'Live Telegram delivery');
  } else {
    run(`"${NODE}" scripts/egx_prod_prepare_send.mjs --date ${signalDate} --skip-score`, 'Prepare (dry)', { optional: true });
    run(`"${NODE}" scripts/egx_telegram_daily.mjs --dry-run`, 'Telegram dry-run');
  }

  const reconcileOk = run(
    `"${NODE}" scripts/egx_notify_reconcile.mjs`,
    'Post-send reconcile',
    { optional: true },
  );

  if (!reconcileOk) {
    alertNotification('RECONCILE_GAP', { signal_date: signalDate });
    logDeliveryAttempt({
      signal_date: signalDate,
      skip_reason: 'CRON_RECONCILE_PENDING',
      pipeline_stage: 'cron_reconcile_warn',
      dedup_key: `cron:reconcile:${signalDate}`,
    });
    process.exit(3);
  }

  run(`"${NODE}" scripts/egx_notification_pipeline_audit.mjs --date ${signalDate}`, 'Pipeline audit', { optional: true });

  if (!DRY_RUN && process.env.EGX_PORTFOLIO_AUTO === '1') {
    const PY = process.env.PYTHON_BIN || '/usr/bin/python3';
    run(`"${PY}" scripts/python/portfolio_tracker.py import_signals`, 'Portfolio import signals', { optional: true });
    run(`"${PY}" scripts/python/portfolio_tracker.py daily`, 'Portfolio daily update', { optional: true });
  }

  run(`"${NODE}" scripts/egx_export_trades_csv.mjs`, 'Export trades.csv', { optional: true });

  await opsSuccessAlert('CRON_DELIVERY_OK', buildDeliveryDigest(signalDate));

  console.log('\n═══ Telegram Cron SUCCESS ═══\n');
  process.exit(0);
} catch (e) {
  alertNotification('CRON_DELIVERY_FAILED', {
    signal_date: signalDate,
    error: e.message?.slice(0, 500),
  });
  logDeliveryAttempt({
    signal_date: signalDate,
    skip_reason: `CRON_FAILED:${e.message?.slice(0, 300)}`,
    pipeline_stage: 'cron_failed',
    dedup_key: `cron:fail:${signalDate}`,
    meta_json: { dry_run: DRY_RUN },
  });
  console.error('\n═══ Telegram Cron FAILED ═══\n');
  process.exit(1);
}
