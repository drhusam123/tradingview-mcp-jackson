/**
 * Phase 20 — live session day gate (anchor 2026-06-17) + t5 watch closure gate.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluateLiveSessionValidation } from './phase18_live_ops.mjs';
import { evaluatePostGraduationSession } from './post_graduation_session.mjs';

const LIVE_ANCHOR = process.env.EGX_LIVE_SESSION_ANCHOR ?? process.env.EGX_POST_GRAD_SESSION_DATE ?? '2026-06-17';
const T5_CLOSURE_ANCHOR = process.env.EGX_T5_CLOSURE_ANCHOR ?? '2026-06-19';
const STATE_FILE = 'live_session_day_state.json';

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function writeState(state) {
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data', STATE_FILE), JSON.stringify(state, null, 2));
}

/** Validate and persist live session on anchor trading day. */
export function evaluateLiveSessionDayGate(signalDate) {
  const live = evaluateLiveSessionValidation(signalDate);
  const postGrad = evaluatePostGraduationSession(signalDate);
  let state = readJson(STATE_FILE) ?? {};

  const isAnchorDay = signalDate === LIVE_ANCHOR;
  const sessionOk = live.pass;
  const anchorBoot = process.env.EGX_LIVE_ANCHOR_BOOTSTRAP === '1';
  const prevalidate = process.env.EGX_LIVE_ANCHOR_PREVALIDATE === '1';

  if (
    anchorBoot && prevalidate && sessionOk && !state.anchor_validated
    && signalDate < LIVE_ANCHOR
  ) {
    state = {
      anchor_date: LIVE_ANCHOR,
      anchor_validated: true,
      prevalidated: true,
      signal_date: signalDate,
      deliverable: live.deliverable,
      symbols: live.symbols,
      sent: live.sent_today,
      correlation_summary: live.correlation_summary,
      validated_at: new Date().toISOString(),
      note: `pre-validated on ${signalDate} — anchor ${LIVE_ANCHOR}`,
    };
    writeState(state);
  }

  if (isAnchorDay && sessionOk && !state.anchor_validated) {
    state = {
      anchor_date: LIVE_ANCHOR,
      anchor_validated: true,
      signal_date: signalDate,
      deliverable: live.deliverable,
      symbols: live.symbols,
      sent: live.sent_today,
      correlation_summary: live.correlation_summary,
      validated_at: new Date().toISOString(),
    };
    writeState(state);
  }

  return {
    anchor_date: LIVE_ANCHOR,
    is_anchor_day: isAnchorDay,
    anchor_validated: Boolean(state.anchor_validated),
    validated_on: state.signal_date ?? null,
    current_session_pass: sessionOk,
    live_session: live,
    post_graduation: postGrad,
    pass: isAnchorDay
      ? sessionOk
      : (state.anchor_validated || (anchorBoot && signalDate < LIVE_ANCHOR)),
    detail: state.anchor_validated
      ? (state.prevalidated
        ? `pre-validated ${state.signal_date} → anchor ${LIVE_ANCHOR}`
        : `anchor session validated ${state.signal_date}`)
      : isAnchorDay
        ? (sessionOk ? 'anchor day OK' : 'anchor day validation pending')
        : signalDate < LIVE_ANCHOR
          ? `awaiting anchor ${LIVE_ANCHOR}`
          : `anchor missed — run recovery for ${LIVE_ANCHOR}`,
  };
}

export function evaluateT5WatchClosureGate(signalDate) {
  const closure = readJson('p6_watch_t5_closure_last.json');
  const onOrAfter = signalDate >= T5_CLOSURE_ANCHOR;

  return {
    closure_anchor: T5_CLOSURE_ANCHOR,
    on_or_after_anchor: onOrAfter,
    closure_met: Boolean(closure?.closure_met),
    gate_pass: closure?.gate_pass !== false,
    watch: closure?.watch ?? [],
    status_line: closure?.status_line ?? 'not run',
    delivered_closed: closure?.delivered_closed ?? null,
    pass: onOrAfter ? Boolean(closure?.closure_met) : true,
    detail: onOrAfter
      ? (closure?.closure_met ? 'watch t5 CLOSED' : closure?.status_line ?? 't5 pending')
      : `pre-closure (${T5_CLOSURE_ANCHOR}) — monitoring`,
  };
}

export function writeLiveSessionDaySnapshot(signalDate = null) {
  const snap = evaluateLiveSessionDayGate(signalDate);
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/live_session_day_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
