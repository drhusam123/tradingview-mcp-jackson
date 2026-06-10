/**
 * Pre-send gate — mandatory checks before any live client notification.
 */
import { existsSync, readFileSync } from 'fs';
import { isTelegramConfigured, telegramStatus } from '../../src/egx/notify.js';
import {
  countActionable, getUpstreamDates, upstreamIssues, latestOhlcvDate,
  wasAlreadySent, logDeliveryAttempt, normalizeDeliverableSignals,
} from './delivery_audit.mjs';
import { ensureUpstreamFresh } from './ensure_upstream_fresh.mjs';
import { alertNotification } from './notification_alert.mjs';
import { runEgxSafetyCheck } from './egx_safety_check.mjs';

const DRY_RUN_ENV = process.env.EGX_NOTIFY_DRY_RUN === '1';

import { join as pathJoin, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = pathJoin(dirname(fileURLToPath(import.meta.url)), '../..');
const LEGACY_PATH = pathJoin(ROOT, 'data/telegram_delivery_log.json');

function legacyDuplicateToday(signalDate) {
  if (!existsSync(LEGACY_PATH)) return false;
  try {
    const log = JSON.parse(readFileSync(LEGACY_PATH, 'utf8'));
    return (log.deliveries || []).some(d => d.date === signalDate && d.messages_sent > 0);
  } catch {
    return false;
  }
}

/**
 * @param {string} reportDate
 * @param {object} opts
 */
export function runPreSendCheck(reportDate, opts = {}) {
  const {
    dryRun = false,
    skipMlRemediate = false,
    allowDuplicate = false,
    skipLegacyDedup = false,
    logBlock = true,
  } = opts;

  normalizeDeliverableSignals(reportDate);
  const act = countActionable(reportDate);
  const upstreamBefore = getUpstreamDates();
  const checks = [];

  function record(ok, name, detail = '') {
    checks.push({ ok, name, detail });
    return ok;
  }

  record(Boolean(reportDate), 'latest_signal_date', reportDate);
  const ohlcv = latestOhlcvDate();
  record(Boolean(ohlcv && ohlcv >= reportDate), 'ohlcv_date', `ohlcv=${ohlcv ?? 'none'} required=${reportDate}`);

  let upstreamResult = { ok: upstreamIssues(reportDate).length === 0, dates: upstreamBefore, issues: upstreamIssues(reportDate) };
  if (!dryRun && !skipMlRemediate && upstreamResult.issues.length > 0) {
    upstreamResult = ensureUpstreamFresh(reportDate, { autoRemediate: true, logAudit: false });
  }
  const dates = upstreamResult.dates || getUpstreamDates();
  const mlOk = dates.ml_pred && dates.ml_pred >= reportDate;
  record(mlOk, 'ml_prediction_date', `latest=${dates.ml_pred ?? 'none'} required=${reportDate}`);
  record(!dates.scan || dates.scan >= reportDate, 'scan_date', `latest=${dates.scan ?? 'none'}`);
  record(!dates.meta || dates.meta >= reportDate, 'meta_label_date', `latest=${dates.meta ?? 'none'}`);

  record(true, 'actionable_count', `${act.db}`);
  record(true, 'deliverable_count', `${act.deliverable} symbols=${act.symbols.join(',') || 'none'}`);

  const safetyVeto = process.env.EGX_SAFETY_VETO !== '0';
  let safety = null;
  let safetyExtraBlockers = [];
  if (act.deliverable > 0 && safetyVeto) {
    safety = runEgxSafetyCheck(reportDate, { veto: true });
    const safetyOk = safety.ok && safety.deliverable_after > 0;
    record(
      safetyOk || act.deliverable === 0,
      'safety_veto',
      `passed=${safety.passed_symbols.join(',') || 'none'} blocked=${safety.blocked_symbols.join(',') || 'none'}`,
    );
    if (!safetyOk && act.deliverable > 0) {
      safetyExtraBlockers.push(`safety_veto: ${safety.blocked_symbols.join(',')}`);
    }
  } else {
    record(true, 'safety_veto', safetyVeto ? 'no deliverable' : 'EGX_SAFETY_VETO=0');
  }
  record(isTelegramConfigured(), 'telegram_env_ok', JSON.stringify(telegramStatus()));
  record(Boolean(process.env.TELEGRAM_CHAT_ID), 'recipients_ok', process.env.TELEGRAM_CHAT_ID || 'missing');
  record(!DRY_RUN_ENV || dryRun, 'dry_run_mode', DRY_RUN_ENV ? 'EGX_NOTIFY_DRY_RUN=1 blocks live' : 'off');

  const dup = wasAlreadySent(reportDate);
  const legacyDup = skipLegacyDedup ? false : legacyDuplicateToday(reportDate);
  const dedupOk = allowDuplicate || (!dup.duplicate && !legacyDup);
  record(dedupOk, 'dedup_ok', dup.duplicate ? dup.reason : (legacyDup ? 'legacy_log_duplicate' : 'ok'));

  const blockers = checks.filter(c => !c.ok).map(c => `${c.name}: ${c.detail}`);
  blockers.push(...safetyExtraBlockers);
  if (!upstreamResult.ok && upstreamResult.issues?.length) {
    blockers.push(...upstreamResult.issues.map(i => `upstream: ${i}`));
  }

  const ok = blockers.length === 0;
  const result = {
    ok,
    report_date: reportDate,
    dry_run: dryRun,
    checks,
    blockers,
    actionable: act,
    upstream: dates,
    ml_latest_date: dates.ml_pred,
    required_ml_date: reportDate,
    dedup: dup.duplicate ? dup : { duplicate: legacyDup, reason: legacyDup ? 'legacy_log' : null },
    upstream_remediation: upstreamResult.remediated ? upstreamResult : null,
    safety_check: safety ? {
      ok: safety.ok,
      passed: safety.passed_symbols,
      blocked: safety.blocked_symbols,
      deliverable_after: safety.deliverable_after,
    } : null,
  };

  if (!ok && logBlock && !dryRun) {
    const skipReason = !mlOk ? 'ML_STALE' : blockers[0]?.split(':')[0] || 'PRE_SEND_BLOCK';
    alertNotification(skipReason, {
      signal_date: reportDate,
      blockers,
      ml_latest_date: dates.ml_pred,
      required_ml_date: reportDate,
      deliverable: act.deliverable,
    });
    result.audit_id = logDeliveryAttempt({
      signal_date: reportDate,
      actionable: act.db > 0,
      deliverable: act.deliverable > 0,
      message_generated: 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `${skipReason}: ${blockers.join(' | ')}`,
      pipeline_stage: 'pre_send_block',
      ml_latest_date: dates.ml_pred,
      required_ml_date: reportDate,
      dedup_key: `pre_send_block:${reportDate}`,
      meta_json: { checks, blockers, required_commands: upstreamResult.required_commands },
    });
  }

  return result;
}

export function assertPreSendGreen(reportDate, opts = {}) {
  const r = runPreSendCheck(reportDate, { ...opts, logBlock: true });
  if (!r.ok) {
    const err = new Error(`Pre-send check failed: ${r.blockers.join('; ')}`);
    err.preSend = r;
    throw err;
  }
  return r;
}
