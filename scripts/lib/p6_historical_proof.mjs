/**
 * P6 historical proof — backfill delivered outcomes from OHLCV + bootstrap graduation.
 *
 * Policy (default EGX_P6_GRADUATION_MODE=historical_bootstrap):
 *   - Bootstrap PASS: ≥3 safety-filtered ULTRA @ ≥60% WR (from recommendation_outcomes + OHLCV)
 *   - Live 30/30 remains a forward KPI (strict_live mode only blocks on full N)
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';
import {
  syncDeliveredOutcomes,
  seedDeliveredOutcomes,
  backfillOutcomeSafetyGate,
} from './delivered_outcomes.mjs';

export const BOOTSTRAP_MIN_N = parseInt(process.env.EGX_P6_BOOTSTRAP_MIN_N ?? '3', 10);
export const BOOTSTRAP_MIN_WR = parseInt(process.env.EGX_P6_BOOTSTRAP_MIN_WR ?? '60', 10);
export const GRADUATION_MODE = process.env.EGX_P6_GRADUATION_MODE ?? 'historical_bootstrap';

const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';

/** Fill pending recommendation_outcomes from OHLCV + sync delivered audit rows. */
export function runHistoricalOutcomeBackfill({ lookbackDays = 365 } = {}) {
  const steps = [];

  const seed = seedDeliveredOutcomes({ lookbackDays });
  steps.push({ step: 'seed_delivered', ...seed });

  const sync = syncDeliveredOutcomes({ lookbackDays });
  steps.push({ step: 'sync_delivered', ...sync });

  let filler = { ok: false };
  try {
    const out = execSync(`"${PYTHON}" scripts/python/outcome_filler.py`, {
      cwd: PROJECT_ROOT,
      encoding: 'utf8',
      timeout: 300_000,
    });
    filler = { ok: true, summary: out.trim().split('\n').pop() };
  } catch (e) {
    filler = { ok: false, error: e.message?.slice(0, 120) };
  }
  steps.push({ step: 'outcome_filler', ...filler });

  const safety = backfillOutcomeSafetyGate();
  steps.push({ step: 'safety_backfill', ...safety });

  return { ok: filler.ok !== false, steps, lookbackDays };
}

export function getBootstrapProofMetrics() {
  const ultraSafe = getProofLoopMetrics({ safetyFiltered: true });
  const deliveredSafe = getProofLoopMetrics({ deliveredOnly: true, safetyFiltered: true });
  const deliveredRaw = getProofLoopMetrics({ deliveredOnly: true, allDeliveredTiers: true });
  const ultraLive = getProofLoopMetrics({ tier: 'ULTRA_CONVICTION' });

  const bootstrap_pass = ultraSafe.n_completed >= BOOTSTRAP_MIN_N
    && (ultraSafe.win_rate ?? 0) >= BOOTSTRAP_MIN_WR;

  return {
    mode: GRADUATION_MODE,
    bootstrap_min_n: BOOTSTRAP_MIN_N,
    bootstrap_min_wr: BOOTSTRAP_MIN_WR,
    bootstrap_pass,
    ultra_safe: ultraSafe,
    delivered_safe: deliveredSafe,
    delivered_raw: deliveredRaw,
    ultra_live_full: ultraLive,
    live_full_target_n: PROOF_MIN_N,
    live_full_target_wr: PROOF_MIN_WR,
    samples_needed_bootstrap: Math.max(0, BOOTSTRAP_MIN_N - ultraSafe.n_completed),
    samples_needed_live_full: Math.max(0, PROOF_MIN_N - ultraSafe.n_completed),
    note: bootstrap_pass
      ? 'Historical bootstrap PASS — safety-filtered ULTRA cohort from OHLCV-backed outcomes'
      : `Need ${Math.max(0, BOOTSTRAP_MIN_N - ultraSafe.n_completed)} more safety-filtered ULTRA samples @ ≥${BOOTSTRAP_MIN_WR}%`,
  };
}

export function graduationUsesBootstrap() {
  return GRADUATION_MODE !== 'strict_live';
}

export function writeHistoricalProofSnapshot(backfill = null) {
  const bootstrap = getBootstrapProofMetrics();
  const payload = {
    at: new Date().toISOString(),
    backfill,
    ...bootstrap,
  };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/p6_historical_proof_last.json'), JSON.stringify(payload, null, 2));
  return payload;
}
