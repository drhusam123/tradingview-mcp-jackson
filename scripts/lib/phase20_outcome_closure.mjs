/**
 * Phase 20 — outcome closure + live session day evaluator.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { latestOhlcvDate } from './delivery_audit.mjs';
import { evaluatePhase19SessionOps } from './phase19_session_ops.mjs';
import {
  evaluateLiveSessionDayGate,
  evaluateT5WatchClosureGate,
} from './live_session_day_gate.mjs';
import { evaluateLreOosGate } from './lre_oos_gate.mjs';
import { evaluateWeeklyProdReady } from './weekly_prod_ready.mjs';
import { getLiveKpiDashboard } from './p6_live_kpi.mjs';
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

export function evaluatePhase20OutcomeClosure(signalDate = latestOhlcvDate()) {
  const phase19 = evaluatePhase19SessionOps(signalDate);
  const liveDay = evaluateLiveSessionDayGate(signalDate);
  const t5Closure = evaluateT5WatchClosureGate(signalDate);
  const lreOos = evaluateLreOosGate();
  const lreAcc = readJson('lre_oos_accumulator_last.json');
  const weeklyProd = evaluateWeeklyProdReady();
  const kpi = getLiveKpiDashboard();
  const nxt = nextTradingDay(cairoDateParts().date);

  const gates = {
    production_graduated: phase19.gates.production_graduated,
    live_session_anchor: {
      pass: liveDay.pass,
      detail: liveDay.detail,
      anchor: liveDay.anchor_date,
      validated: liveDay.anchor_validated,
    },
    t5_watch_closure: {
      pass: t5Closure.pass,
      detail: t5Closure.detail,
      closure_anchor: t5Closure.closure_anchor,
      closure_met: t5Closure.closure_met,
      delivered_n: t5Closure.delivered_closed?.n,
    },
    p6_delivered_kpi: {
      pass: Boolean(readJson('p6_watch_t5_closure_last.json')?.success),
      detail: t5Closure.status_line,
      live_ultra: kpi?.ultra_safe ? `${kpi.ultra_safe.n}/${kpi.ultra_safe.target}` : kpi?.status_line,
      delivered_safe_n: kpi?.delivered?.safe_n,
      delivered_safe_wr: kpi?.delivered?.safe_wr,
    },
    lre_oos: {
      pass: Boolean(lreAcc?.success),
      closed: lreAcc?.oos_closed ?? lreOos.oos_closed,
      target: lreAcc?.oos_target ?? lreOos.oos_target,
      detail: lreAcc?.status_line ?? lreOos.reason,
    },
    live_session: phase19.gates.live_session,
  };

  const phase20_ready = gates.production_graduated.pass
    && gates.t5_watch_closure.pass !== false
    && gates.p6_delivered_kpi.pass;

  const blockers = [];
  if (!gates.production_graduated.pass) blockers.push('production not graduated');
  if (liveDay.is_anchor_day && !liveDay.pass) blockers.push(`live anchor ${liveDay.anchor_date} not validated`);
  if (t5Closure.on_or_after_anchor && !t5Closure.closure_met) {
    blockers.push('watch t5 closure pending (EGCH/UEFM)');
  }
  if (weeklyProd.needs_full_run && !weeklyProd.last_pass) {
    blockers.push('weekly prod:ready:full due');
  }

  return {
    at: new Date().toISOString(),
    phase20_ready,
    signal_date: signalDate,
    next_session: nxt.next_trading_day,
    gates,
    blockers,
    live_session_day: liveDay,
    t5_closure: t5Closure,
    lre_accumulator: lreAcc,
    live_kpi: kpi,
    phase19,
  };
}

export function writePhase20Snapshot(payload = null) {
  const snap = payload ?? evaluatePhase20OutcomeClosure();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/phase20_outcome_closure_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
