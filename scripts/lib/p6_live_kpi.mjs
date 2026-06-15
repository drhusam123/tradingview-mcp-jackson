/**
 * P6 live KPI dashboard — forward monitor 3→30 ULTRA (non-blocking).
 */
import { writeFileSync, mkdirSync, existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import {
  getBootstrapProofMetrics,
  GRADUATION_MODE,
  BOOTSTRAP_MIN_N,
} from './p6_historical_proof.mjs';
import { listPendingDeliveredOutcomes } from './p6_graduation_gate.mjs';

function readJson(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

export function getLiveKpiDashboard() {
  const bootstrap = getBootstrapProofMetrics();
  const ultraSafe = bootstrap.ultra_safe;
  const deliveredSafe = bootstrap.delivered_safe;
  const deliveredRaw = bootstrap.delivered_raw;
  const pending = listPendingDeliveredOutcomes();

  const medShadow = readJson(join(PROJECT_ROOT, 'data/med_client_signal_shadow_last.json'));
  const medAb = readJson(join(PROJECT_ROOT, 'data/med_feed_ab_last.json'));
  const mdePilot = readJson(join(PROJECT_ROOT, 'data/mde_pilot_shadow_last.json'));

  const ultraPct = PROOF_MIN_N > 0
    ? Math.round((ultraSafe.n_completed / PROOF_MIN_N) * 1000) / 10
    : 0;

  return {
    at: new Date().toISOString(),
    mode: GRADUATION_MODE,
    blocking: false,
    bootstrap_pass: bootstrap.bootstrap_pass,
    ultra_safe: {
      n: ultraSafe.n_completed,
      target: PROOF_MIN_N,
      wr: ultraSafe.win_rate,
      target_wr: PROOF_MIN_WR,
      progress_pct: ultraPct,
      samples_needed: bootstrap.samples_needed_live_full,
      gate_pass: ultraSafe.gate_pass,
    },
    bootstrap: {
      n: ultraSafe.n_completed,
      target: BOOTSTRAP_MIN_N,
      wr: ultraSafe.win_rate,
      pass: bootstrap.bootstrap_pass,
    },
    delivered: {
      safe_n: deliveredSafe.n_completed,
      safe_wr: deliveredSafe.win_rate,
      raw_n: deliveredRaw.n,
      raw_wr: deliveredRaw.wr,
      pending_t5: pending.length,
    },
    phase13: {
      med_client_shadow: {
        sessions: medShadow?.shadow_sessions ?? 0,
        target_sessions: medShadow?.target_sessions ?? 5,
        validation_pass: medShadow?.validation_pass ?? false,
      },
      med_feed_ab: {
        production_track: medAb?.production_track ?? 'penalize',
        boost_wins: medAb?.boost_wins ?? 0,
        penalize_wins: medAb?.penalize_wins ?? 0,
      },
      mde_pilot: {
        enabled: mdePilot?.pilot_enabled ?? false,
        memory_active: mdePilot?.memory_active ?? false,
        pilot_count: mdePilot?.pilot_count ?? 0,
      },
    },
    status_line: `ULTRA ${ultraSafe.n_completed}/${PROOF_MIN_N} @ ${ultraSafe.win_rate ?? '—'}% | bootstrap ${bootstrap.bootstrap_pass ? 'PASS' : 'pending'} | non-blocking`,
  };
}

export function writeLiveKpiSnapshot() {
  const dash = getLiveKpiDashboard();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/p6_live_kpi_last.json'), JSON.stringify(dash, null, 2));
  return dash;
}
