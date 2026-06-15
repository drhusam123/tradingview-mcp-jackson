/**
 * P6 / client-beta graduation readiness — when to enable research→client paths.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { DB_PATH } from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import {
  getBootstrapProofMetrics,
  graduationUsesBootstrap,
  BOOTSTRAP_MIN_N,
  BOOTSTRAP_MIN_WR,
  GRADUATION_MODE,
} from './p6_historical_proof.mjs';
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

function readMdeShadowPilot() {
  const p = join(PROJECT_ROOT, 'data/mde_shadow_promotion_hints.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/** Evaluate all graduation gates and recommended env toggles. */
export function evaluateGraduationReadiness() {
  const bootstrap = getBootstrapProofMetrics();
  const useBootstrap = graduationUsesBootstrap();
  const ultra = bootstrap.ultra_safe;
  const deliveredSafe = bootstrap.delivered_safe;
  const deliveredRaw = bootstrap.delivered_raw;
  const pending = listPendingDeliveredOutcomes();

  const ultraPass = useBootstrap ? bootstrap.bootstrap_pass : ultra.gate_pass;
  const deliveredPass = useBootstrap ? bootstrap.bootstrap_pass : deliveredSafe.gate_pass;
  const medClientReady = useBootstrap ? bootstrap.bootstrap_pass : deliveredSafe.gate_pass;

  const lre = readLreStatus();
  const med = readMedStatus();
  const mdePilot = readMdeShadowPilot();

  const lreOos = lre?.graduation?.progress?.live_oos_closed
    ?? lre?.forward_oos_closed
    ?? lre?.live_oos?.closed
    ?? 0;
  const lreTarget = lre?.graduation?.progress?.target_oos
    ?? lre?.forward_oos_target
    ?? 40;
  const wfPf = lre?.graduation?.progress?.historical_wf_pf_100
    ?? lre?.walk_forward_baseline?.primary_capped_pf_100;
  const lreBoot = process.env.EGX_LRE_OOS_BOOTSTRAP === '1';
  const lreOosReady = lreOos >= lreTarget
    || (lreBoot && (wfPf ?? 0) >= 1.3);

  const medGradMet = Boolean(
    med?.graduation?.graduation_met
    || med?.graduation?.ready_for_feed_boost
    || med?.forward_shadow?.graduation_met,
  );

  const gates = {
    p6_ultra_safe: {
      pass: ultraPass,
      mode: GRADUATION_MODE,
      n: ultra.n_completed,
      wr: ultra.win_rate,
      samples_needed: useBootstrap ? bootstrap.samples_needed_bootstrap : ultra.samples_needed,
      target_n: useBootstrap ? BOOTSTRAP_MIN_N : PROOF_MIN_N,
      target_wr: useBootstrap ? BOOTSTRAP_MIN_WR : PROOF_MIN_WR,
      live_kpi_n: ultra.n_completed,
      live_kpi_target_n: PROOF_MIN_N,
      live_samples_needed: bootstrap.samples_needed_live_full,
    },
    p6_delivered_safe: {
      pass: deliveredPass,
      mode: GRADUATION_MODE,
      n: deliveredSafe.n_completed,
      wr: deliveredSafe.win_rate,
      samples_needed: useBootstrap ? 0 : deliveredSafe.samples_needed,
      target_n: useBootstrap ? BOOTSTRAP_MIN_N : PROOF_MIN_N,
      target_wr: useBootstrap ? BOOTSTRAP_MIN_WR : PROOF_MIN_WR,
      bootstrap_proxy: useBootstrap,
    },
    p6_bootstrap: {
      pass: bootstrap.bootstrap_pass,
      mode: GRADUATION_MODE,
      n: ultra.n_completed,
      wr: ultra.win_rate,
      target_n: BOOTSTRAP_MIN_N,
      target_wr: BOOTSTRAP_MIN_WR,
      source: 'historical_ohlcv_safety_filtered_ultra',
    },
    med_client_signal: {
      pass: medClientReady,
      env: 'MED_CLIENT_SIGNAL',
      current: process.env.MED_CLIENT_SIGNAL ?? '0',
      recommended: medClientReady ? '1' : '0',
      reason: medClientReady
        ? (useBootstrap
          ? `Bootstrap PASS — ${ultra.n_completed}/${BOOTSTRAP_MIN_N} ULTRA safe @ ${ultra.win_rate}% (historical OHLCV)`
          : 'P6 delivered gate PASS — operator may enable MED_CLIENT_SIGNAL=1')
        : (useBootstrap
          ? `Bootstrap needs ${bootstrap.samples_needed_bootstrap} more ULTRA safe samples`
          : `Need ${deliveredSafe.samples_needed} more safe-delivered samples`),
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
    mde_shadow_pilot: {
      pass: Boolean(mdePilot?.pilot_eligible),
      pilot_count: mdePilot?.pilot_count ?? 0,
      env: 'EGX_MDE_PILOT_PROMOTE',
      current: process.env.EGX_MDE_PILOT_PROMOTE ?? '0',
      recommended: mdePilot?.pilot_eligible ? '1' : '0',
      reason: mdePilot?.pilot_eligible
        ? `${mdePilot.pilot_count} shadow hints ready — operator may set EGX_MDE_PILOT_PROMOTE=1`
        : 'Run phase11 / mde_promotion_bridge for shadow hints',
    },
  };

  const blockers = [];
  if (!useBootstrap) {
    if (!gates.p6_ultra_safe.pass && gates.p6_ultra_safe.samples_needed === 0) {
      blockers.push(`P6 ULTRA WR ${ultra.win_rate}% < ${PROOF_MIN_WR}%`);
    } else if (gates.p6_ultra_safe.samples_needed > 0) {
      blockers.push(`P6 ULTRA needs ${gates.p6_ultra_safe.samples_needed} more safe samples`);
    }
    if (gates.p6_delivered_safe.samples_needed > 0) {
      blockers.push(`P6 delivered needs ${gates.p6_delivered_safe.samples_needed} more safe samples`);
    }
  } else if (!bootstrap.bootstrap_pass) {
    blockers.push(`Bootstrap: need ${bootstrap.samples_needed_bootstrap} more safety-filtered ULTRA @ ≥${BOOTSTRAP_MIN_WR}%`);
  }
  if (pending.length && !useBootstrap) {
    blockers.push(`${pending.length} delivered signals awaiting t5 fill`);
  }

  const liveKpiNote = useBootstrap && bootstrap.bootstrap_pass
    ? `Live forward KPI: ${ultra.n_completed}/${PROOF_MIN_N} (non-blocking)`
    : null;

  const nextSession = nextTradingDay(cairoDateParts().date).next_trading_day;
  const nextActions = [
    { when: 'daily', cmd: 'npm run egx:post:session', note: 'closed loop + P6 sync' },
    { when: 'weekly', cmd: 'npm run egx:phase10:graduation', note: 'graduation readiness' },
    { when: nextSession, cmd: 'npm run egx:prod:prepare-send && npm run egx:telegram:cron', note: 'live session' },
  ];

  return {
    at: new Date().toISOString(),
    graduation_mode: GRADUATION_MODE,
    client_beta_ready: deliveredPass,
    research_to_client_ready: ultraPass && deliveredPass,
    bootstrap: {
      pass: bootstrap.bootstrap_pass,
      n: ultra.n_completed,
      wr: ultra.win_rate,
      target_n: BOOTSTRAP_MIN_N,
      live_kpi_note: liveKpiNote,
    },
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
