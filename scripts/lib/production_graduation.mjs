/**
 * Phase 16 — live beta session monitor + production graduation evaluator.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import {
  DB_PATH, getAuditForDate, latestOhlcvDate,
} from './delivery_audit.mjs';
import { buildDeliveryDigest, reconcileCounts } from './ops_digest.mjs';
import { evaluateClientBetaSignoff } from './client_beta_signoff.mjs';
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

const LIVE_MED_SESSIONS_TARGET = parseInt(process.env.EGX_LIVE_MED_SESSIONS_TARGET ?? '3', 10);

/** Monitor post-sign-off live session: Telegram + opp delta + reconcile. */
export function evaluateLiveBetaMonitor(signalDate = latestOhlcvDate()) {
  const signoff = readJson('client_beta_signoff_last.json');
  const medDelta = readJson('med_opp_delta_last.json');
  const digest = buildDeliveryDigest(signalDate);
  const recon = reconcileCounts(14);

  let sentToday = 0;
  let deliverableToday = 0;
  if (signalDate && existsSync(DB_PATH)) {
    const db = new Database(DB_PATH, { readonly: true });
    try {
      deliverableToday = db.prepare(`
        SELECT COUNT(*) n FROM final_signals
        WHERE trade_date=? AND actionable=1 AND (veto_reason IS NULL OR veto_reason='')
      `).get(signalDate)?.n ?? 0;
    } catch { /* */ }
    db.close();
  }

  const audit = signalDate ? getAuditForDate(signalDate) : [];
  sentToday = audit.filter(a =>
    a.send_success === 1 && a.dry_run === 0
    && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage),
  ).length;

  const liveMedSessions = medDelta?.live_sessions_with_client_signal ?? 0;
  const signedOff = Boolean(signoff?.client_beta_signed_off);

  const checks = {
    signed_off: signedOff,
    opp_delta_ok: Boolean(medDelta?.success && (medDelta?.symbols_monitored ?? 0) > 0),
    reconcile_ok: recon.pending === 0,
    med_client_signal_on: medDelta?.MED_CLIENT_SIGNAL === '1' || signoff?.env?.MED_CLIENT_SIGNAL === '1',
    live_med_sessions: liveMedSessions >= LIVE_MED_SESSIONS_TARGET,
    delivery_path_ok: Boolean(process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID),
  };

  const blockers = [];
  if (!signedOff) blockers.push('client beta not signed off');
  if (!checks.opp_delta_ok) blockers.push('opp delta not logged');
  if (!checks.reconcile_ok) blockers.push(`reconcile pending: ${recon.pending}`);

  return {
    at: new Date().toISOString(),
    signal_date: signalDate,
    signed_off: signedOff,
    deliverable_today: deliverableToday,
    sent_today: sentToday,
    reconcile: digest.reconcile,
    pending_reconcile: recon.pending,
    opp_delta: {
      symbols: medDelta?.symbols_monitored ?? 0,
      avg_delta: medDelta?.avg_delta_boost_vs_pen ?? null,
      live_sessions: liveMedSessions,
      target_sessions: LIVE_MED_SESSIONS_TARGET,
    },
    checks,
    healthy: signedOff && checks.opp_delta_ok && checks.reconcile_ok,
    blockers,
    recommended: {
      live_med_sessions: `${liveMedSessions}/${LIVE_MED_SESSIONS_TARGET}`,
      live_med_sessions_ok: checks.live_med_sessions,
    },
  };
}

/** Production graduation = sign-off + prod:ready + live beta healthy. */
export function evaluateProductionGraduation(signalDate = latestOhlcvDate()) {
  const signoff = evaluateClientBetaSignoff();
  const p14 = evaluatePhase14Readiness();
  const liveBeta = evaluateLiveBetaMonitor(signalDate);
  const prodReady = readJson('prod_ready_last.json');
  const medAb = readJson('med_feed_ab_last.json');
  const mdeStability = readJson('mde_pilot_stability_last.json');
  const env = signoff.env ?? readJson('research_client_env.json')?.env ?? {};

  const prodReadyPass = prodReady?.pass === true;
  const feedBoostReady = Boolean(p14.feed_boost_ready);
  const mdeMemoryReady = Boolean(p14.mde_memory_ready);

  const gates = {
    client_beta_signoff: {
      pass: signoff.client_beta_signed_off,
      detail: `${signoff.required_pass}/${signoff.required_total} required`,
    },
    prod_ready: {
      pass: prodReadyPass,
      detail: prodReadyPass ? 'prod:ready PASS' : 'run npm run egx:prod:ready:full',
      last_at: prodReady?.at ?? null,
    },
    live_beta_monitor: {
      pass: liveBeta.healthy,
      detail: liveBeta.blockers.length ? liveBeta.blockers.join('; ') : 'healthy',
    },
    med_feed_boost: {
      pass: feedBoostReady,
      env: 'MED_FEED_BOOST',
      recommended: feedBoostReady ? '1' : '0',
      streak: medAb?.boost_win_streak ?? 0,
      target: medAb?.boost_streak_target ?? 5,
    },
    mde_behavior_memory: {
      pass: mdeMemoryReady,
      env: 'EGX_MDE_BEHAVIOR_MEMORY',
      recommended: mdeMemoryReady ? '1' : '0',
      days: mdeStability?.days_active ?? 0,
      target_days: mdeStability?.target_days ?? 14,
    },
  };

  const production_graduated = gates.client_beta_signoff.pass
    && gates.prod_ready.pass
    && gates.live_beta_monitor.pass;

  const blockers = [];
  if (!gates.client_beta_signoff.pass) blockers.push('client beta sign-off incomplete');
  if (!gates.prod_ready.pass) blockers.push('prod:ready not PASS');
  if (!gates.live_beta_monitor.pass) blockers.push(...liveBeta.blockers);

  return {
    at: new Date().toISOString(),
    production_graduated,
    gates,
    blockers,
    signoff,
    live_beta: liveBeta,
    prod_ready: prodReady,
    promotions: {
      med_feed_boost: feedBoostReady ? 'enable MED_FEED_BOOST=1' : p14.gates.med_feed_ab.reason,
      mde_memory: mdeMemoryReady ? 'enable EGX_MDE_BEHAVIOR_MEMORY=1' : p14.gates.mde_behavior_memory.reason,
    },
    effective_env: env,
  };
}

export function writeProductionGraduationSnapshot(payload = null) {
  const snap = payload ?? evaluateProductionGraduation();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/production_graduation_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
