/**
 * Phase 19 — post-graduation session ops evaluator.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { latestOhlcvDate } from './delivery_audit.mjs';
import { evaluatePhase18LiveOps } from './phase18_live_ops.mjs';
import { evaluatePostGraduationSession } from './post_graduation_session.mjs';
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

export function evaluatePhase19SessionOps(signalDate = latestOhlcvDate()) {
  const phase18 = evaluatePhase18LiveOps(signalDate);
  const postGrad = evaluatePostGraduationSession(signalDate);
  const t5Fill = readJson('p6_t5_fill_last.json');
  const lreAcc = readJson('lre_oos_accumulator_last.json');
  const lreOos = evaluateLreOosGate();
  const weeklyProd = evaluateWeeklyProdReady();
  const nxt = nextTradingDay(cairoDateParts().date);

  const gates = {
    production_graduated: phase18.gates.production_graduated,
    post_graduation_session: {
      pass: postGrad.pass,
      detail: postGrad.detail,
      first_validated: postGrad.first_session_validated,
      anchor: postGrad.anchor_date,
    },
    t5_fill: {
      pass: Boolean(t5Fill?.success),
      detail: t5Fill?.status_line ?? 'not run',
      pending: t5Fill?.pending_after ?? null,
      watch: t5Fill?.watch_pending ?? [],
      newly_closed: t5Fill?.newly_closed_t5?.length ?? 0,
    },
    lre_oos_accumulation: {
      pass: Boolean(lreAcc?.success),
      detail: lreAcc?.status_line ?? lreOos.reason,
      closed: lreAcc?.oos_closed ?? lreOos.oos_closed,
      target: lreAcc?.oos_target ?? lreOos.oos_target,
      delta: lreAcc?.oos_delta ?? 0,
    },
    live_session: phase18.gates.live_session,
    weekly_prod_ready: phase18.gates.weekly_prod_ready,
  };

  const phase19_ready = gates.production_graduated.pass
    && gates.t5_fill.pass
    && gates.lre_oos_accumulation.pass;

  const blockers = [];
  if (!gates.production_graduated.pass) blockers.push('production not graduated');
  if (!gates.post_graduation_session.pass && postGrad.on_or_after_anchor) {
    blockers.push('post-graduation session not validated');
  }
  if (weeklyProd.needs_full_run && !weeklyProd.last_pass) {
    blockers.push('weekly prod:ready:full due — run --weekly-full');
  }

  return {
    at: new Date().toISOString(),
    phase19_ready,
    signal_date: signalDate,
    next_session: nxt.next_trading_day,
    gates,
    blockers,
    post_graduation: postGrad,
    t5_fill: t5Fill,
    lre_accumulator: lreAcc,
    phase18,
  };
}

export function writePhase19Snapshot(payload = null) {
  const snap = payload ?? evaluatePhase19SessionOps();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/phase19_session_ops_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
