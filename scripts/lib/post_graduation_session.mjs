/**
 * Phase 19 — post-graduation first live session tracker.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluateLiveSessionValidation } from './phase18_live_ops.mjs';

const ANCHOR_DATE = process.env.EGX_POST_GRAD_SESSION_DATE ?? '2026-06-17';
const STATE_FILE = 'post_graduation_session_state.json';

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

/** Track and validate first post-graduation live session (default anchor 2026-06-17). */
export function evaluatePostGraduationSession(signalDate = null) {
  const liveSession = evaluateLiveSessionValidation(signalDate);
  const grad = readJson('production_graduation_last.json');
  const correlation = readJson('med_live_delivery_correlation_last.json');
  let state = readJson(STATE_FILE) ?? {};

  const graduated = Boolean(grad?.production_graduated);
  const onOrAfterAnchor = !signalDate || signalDate >= ANCHOR_DATE;
  const sessionOk = liveSession.pass;

  if (graduated && onOrAfterAnchor && sessionOk && !state.first_session_validated) {
    state = {
      ...state,
      first_session_validated: true,
      first_session_date: signalDate ?? liveSession.signal_date,
      deliverable: liveSession.deliverable,
      symbols: liveSession.symbols,
      sent: liveSession.sent_today,
      correlation_summary: correlation?.summary ?? liveSession.correlation_summary,
      validated_at: new Date().toISOString(),
      anchor_date: ANCHOR_DATE,
    };
    writeState(state);
  }

  return {
    at: new Date().toISOString(),
    anchor_date: ANCHOR_DATE,
    graduated,
    signal_date: signalDate ?? liveSession.signal_date,
    first_session_validated: Boolean(state.first_session_validated),
    first_session_date: state.first_session_date ?? null,
    current_session_pass: sessionOk,
    on_or_after_anchor: onOrAfterAnchor,
    live_session: liveSession,
    pass: graduated && (
      (state.first_session_validated && Boolean(state.first_session_date))
      || (onOrAfterAnchor && sessionOk)
    ),
    detail: state.first_session_validated && state.first_session_date
      ? `first session validated ${state.first_session_date}`
      : onOrAfterAnchor
        ? (sessionOk ? 'current session OK — awaiting anchor confirmation' : 'session validation pending')
        : `pre-anchor (${ANCHOR_DATE}) — monitoring`,
  };
}

export function writePostGraduationSessionSnapshot(payload = null) {
  const snap = payload ?? evaluatePostGraduationSession();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/post_graduation_session_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
