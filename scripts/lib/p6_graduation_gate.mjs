/**
 * P6 / client-beta graduation readiness — when to enable research→client paths.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { DB_PATH } from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import { nextTradingDay, cairoDateParts } from './egx_calendar.mjs';

export function listPendingDeliveredOutcomes({ minFilled = 5 } = {}) {
  if (!existsSync(DB_PATH)) return [];
  const db = new Database(DB_PATH, { readonly: true });
  try {
    return db.prepare(`
      SELECT symbol, signal_date, conviction_tier, outcome_filled, client_delivered
      FROM recommendation_outcomes
      WHERE COALESCE(client_delivered, 0) = 1
        AND outcome_filled < ?
      ORDER BY signal_date DESC
      LIMIT 20
    `).all(minFilled);
  } finally {
    db.close();
  }
}

function readLreStatus() {
  const p = join(PROJECT_ROOT, 'data/lre_4_0_status_last.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function readMedStatus() {
  const p = join(PROJECT_ROOT, 'data/med_0_3_status_last.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/** Evaluate all graduation gates and recommended env toggles. */
export function evaluateGraduationReadiness() {
  const ultra = getProofLoopMetrics({ safetyFiltered: true });
  const deliveredSafe = getProofLoopMetrics({ deliveredOnly: true, safetyFiltered: true });
  const deliveredRaw = getProofLoopMetrics({ deliveredOnly: true, allDeliveredTiers: true });
  const pending = listPendingDeliveredOutcomes();

  const lre = readLreStatus();
  const med = readMedStatus();

  const lreOos = lre?.forward_oos_closed ?? lre?.live_oos?.closed ?? 0;
  const lreTarget = lre?.forward_oos_target ?? 40;
  const lreOosReady = lreOos >= lreTarget;

  const medGradMet = Boolean(
    med?.graduation?.graduation_met
    || med?.graduation?.ready_for_feed_boost
    || med?.forward_shadow?.graduation_met,
  );

  const gates = {
    p6_ultra_safe: {
      pass: ultra.gate_pass,
      n: ultra.n_completed,
      wr: ultra.win_rate,
      samples_needed: ultra.samples_needed,
      target_n: PROOF_MIN_N,
      target_wr: PROOF_MIN_WR,
    },
    p6_delivered_safe: {
      pass: deliveredSafe.gate_pass,
      n: deliveredSafe.n_completed,
      wr: deliveredSafe.win_rate,
      samples_needed: deliveredSafe.samples_needed,
      target_n: PROOF_MIN_N,
      target_wr: PROOF_MIN_WR,
    },
    med_client_signal: {
      pass: false,
      env: 'MED_CLIENT_SIGNAL',
      current: process.env.MED_CLIENT_SIGNAL ?? '0',
      recommended: deliveredSafe.gate_pass ? '1' : '0',
      reason: deliveredSafe.gate_pass
        ? 'P6 delivered gate PASS — operator may enable MED_CLIENT_SIGNAL=1'
        : `Need ${deliveredSafe.samples_needed} more safe-delivered samples`,
    },
    med_feed_boost: {
      pass: medGradMet,
      env: 'MED_FEED_BOOST',
      current: process.env.MED_FEED_BOOST ?? '0',
      recommended: medGradMet ? '1' : '0',
      reason: medGradMet ? 'MED graduation met' : 'MED forward OOS accumulating',
    },
    lre_feed_boost: {
      pass: lreOosReady,
      env: 'EGX_LRE_FEED_BOOST',
      current: process.env.EGX_LRE_FEED_BOOST ?? '0',
      recommended: lreOosReady ? '1' : '0',
      oos_closed: lreOos,
      oos_target: lreTarget,
      reason: lreOosReady
        ? 'LRE forward OOS target met'
        : `LRE OOS ${lreOos}/${lreTarget}`,
    },
    mde_client_actionable: {
      pass: false,
      env: 'EGX_MDE_OPP_BOOST',
      current: process.env.EGX_MDE_OPP_BOOST ?? '0',
      recommended: '0',
      reason: 'MDE remains shadow — client-grade validation only',
    },
  };

  const blockers = [];
  if (!gates.p6_ultra_safe.pass && gates.p6_ultra_safe.samples_needed === 0) {
    blockers.push(`P6 ULTRA WR ${ultra.win_rate}% < ${PROOF_MIN_WR}%`);
  } else if (gates.p6_ultra_safe.samples_needed > 0) {
    blockers.push(`P6 ULTRA needs ${gates.p6_ultra_safe.samples_needed} more safe samples`);
  }
  if (gates.p6_delivered_safe.samples_needed > 0) {
    blockers.push(`P6 delivered needs ${gates.p6_delivered_safe.samples_needed} more safe samples`);
  }
  if (pending.length) {
    blockers.push(`${pending.length} delivered signals awaiting t5 fill`);
  }

  const nextSession = nextTradingDay(cairoDateParts().date).next_trading_day;
  const nextActions = [
    { when: 'daily', cmd: 'npm run egx:post:session', note: 'closed loop + P6 sync' },
    { when: 'weekly', cmd: 'npm run egx:phase10:graduation', note: 'graduation readiness' },
    { when: nextSession, cmd: 'npm run egx:prod:prepare-send && npm run egx:telegram:cron', note: 'live session' },
  ];

  return {
    at: new Date().toISOString(),
    client_beta_ready: gates.p6_delivered_safe.pass,
    research_to_client_ready: gates.p6_ultra_safe.pass && gates.p6_delivered_safe.pass,
    gates,
    blockers,
    pending_delivered: pending.map(r => ({
      symbol: r.symbol,
      signal_date: r.signal_date,
      outcome_filled: r.outcome_filled,
      bars_needed: Math.max(0, 5 - (r.outcome_filled ?? 0)),
    })),
    delivered_raw: {
      n: deliveredRaw.n_completed,
      wr: deliveredRaw.win_rate,
    },
    next_actions: nextActions,
  };
}
