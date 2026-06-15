/**
 * Phase 18 — live session validation + LRE OOS + P6 delivered KPI + weekly prod:ready.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import {
  countActionable, getAuditForDate, latestOhlcvDate,
} from './delivery_audit.mjs';
import { evaluateProductionGraduation, evaluateLiveBetaMonitor } from './production_graduation.mjs';
import { evaluateLreOosGate } from './lre_oos_gate.mjs';
import { evaluateWeeklyProdReady } from './weekly_prod_ready.mjs';
import { nextTradingDay, cairoDateParts } from './egx_calendar.mjs';

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/** Validate live send ↔ deliverable ↔ correlation alignment. */
export function evaluateLiveSessionValidation(signalDate = latestOhlcvDate()) {
  const correlation = readJson('med_live_delivery_correlation_last.json');
  const act = signalDate ? countActionable(signalDate) : { deliverable: 0, symbols: [] };
  const audit = signalDate ? getAuditForDate(signalDate) : [];
  const sent = audit.filter(a =>
    a.send_success === 1 && a.dry_run === 0
    && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage),
  ).length;

  const deliveredCorr = correlation?.symbols_delivered ?? 0;
  const reconcileOk = act.deliverable === 0
    ? sent === 0
    : sent >= act.deliverable && deliveredCorr >= act.deliverable;

  return {
    signal_date: signalDate,
    deliverable: act.deliverable,
    symbols: act.symbols,
    sent_today: sent,
    correlation_delivered: deliveredCorr,
    correlation_summary: correlation?.summary ?? null,
    reconcile_ok: reconcileOk,
    pass: Boolean(correlation?.success) && reconcileOk,
  };
}

export function evaluatePhase18LiveOps(signalDate = latestOhlcvDate()) {
  const prod = evaluateProductionGraduation(signalDate);
  const liveBeta = evaluateLiveBetaMonitor(signalDate);
  const liveSession = evaluateLiveSessionValidation(signalDate);
  const p6Delivered = readJson('p6_delivered_kpi_last.json');
  const lreOos = evaluateLreOosGate();
  const weeklyProd = evaluateWeeklyProdReady();
  const promotion = readJson('promotion_activation_last.json');
  const nxt = nextTradingDay(cairoDateParts().date);

  const gates = {
    production_graduated: {
      pass: prod.production_graduated,
      detail: prod.production_graduated ? 'graduated' : prod.blockers.join('; '),
    },
    live_session: {
      pass: liveSession.pass,
      detail: liveSession.correlation_summary ?? `${liveSession.sent_today}/${liveSession.deliverable} sent`,
    },
    live_beta: {
      pass: liveBeta.healthy,
      detail: liveBeta.blockers.length ? liveBeta.blockers.join('; ') : 'healthy',
    },
    p6_delivered_kpi: {
      pass: Boolean(p6Delivered?.success),
      detail: p6Delivered?.status_line ?? 'not run',
      pending: p6Delivered?.pending_count ?? 0,
    },
    lre_oos: {
      pass: lreOos.pass,
      recommended: lreOos.recommended,
      detail: lreOos.reason,
      closed: lreOos.oos_closed,
      target: lreOos.oos_target,
    },
    weekly_prod_ready: {
      pass: !weeklyProd.needs_full_run || weeklyProd.last_pass === true,
      detail: weeklyProd.recommendation,
      needs_full: weeklyProd.needs_full_run,
      age_days: weeklyProd.age_days,
    },
  };

  const phase18_ready = gates.production_graduated.pass
    && gates.p6_delivered_kpi.pass
    && gates.lre_oos.detail;

  const blockers = [];
  if (!gates.production_graduated.pass) blockers.push('production not graduated');
  if (!gates.live_session.pass && gates.live_session.deliverable > 0) {
    blockers.push('live session delivery/correlation mismatch');
  }
  if (weeklyProd.needs_full_run && !weeklyProd.last_pass) {
    blockers.push('weekly prod:ready:full due');
  }

  return {
    at: new Date().toISOString(),
    phase18_ready,
    next_session: nxt.next_trading_day,
    gates,
    blockers,
    live_session: liveSession,
    p6_delivered: p6Delivered,
    lre_oos: lreOos,
    weekly_prod: weeklyProd,
    promotion_verdict: promotion?.verdict ?? null,
    production: prod,
  };
}

export function writePhase18Snapshot(payload = null) {
  const snap = payload ?? evaluatePhase18LiveOps();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/phase18_live_ops_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
