/**
 * Phase 21 — live anchor + t5 closure automation evaluator.
 */
import { latestOhlcvDate } from './delivery_audit.mjs';
import { evaluateLiveSessionDayGate, evaluateT5WatchClosureGate } from './live_session_day_gate.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';

export function evaluatePhase21LiveAnchor(signalDate = latestOhlcvDate()) {
  const anchorBoot = process.env.EGX_LIVE_ANCHOR_BOOTSTRAP === '1';
  const liveDay = evaluateLiveSessionDayGate(signalDate);
  const t5 = evaluateT5WatchClosureGate(signalDate);
  const wr = readJson('p6_delivered_wr_dashboard_last.json');
  const watch = readJson('p6_watch_t5_closure_last.json');

  const anchorPass = anchorBoot
    ? true
    : (liveDay.anchor_validated || (liveDay.is_anchor_day && liveDay.pass));

  const snap = {
    at: new Date().toISOString(),
    phase21_ready: anchorPass && Boolean(wr?.success) && t5.pass !== false,
    signal_date: signalDate,
    gates: {
      live_anchor: {
        pass: anchorPass,
        detail: liveDay.detail,
        bootstrap: anchorBoot,
      },
      t5_closure: {
        pass: t5.pass,
        detail: t5.detail,
        closure_met: t5.closure_met,
      },
      delivered_wr: {
        pass: Boolean(wr?.success),
        detail: wr?.status_line ?? 'not run',
      },
    },
    live_day: liveDay,
    t5,
    wr_dashboard: wr,
    watch,
  };
  writeJson('phase21_live_anchor_last.json', snap);
  return snap;
}
