/**
 * P6 Proof Loop — live outcome metrics for client beta gate.
 * Gate: ULTRA_CONVICTION WinRate ≥ 60% over ≥ 30 completed signals (5d horizon).
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { DB_PATH } from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';

export const PROOF_MIN_N = 30;
export const PROOF_MIN_WR = 60;

export function getProofLoopMetrics({ tier = 'ULTRA_CONVICTION', horizon = 't5' } = {}) {
  if (!existsSync(DB_PATH)) {
    return {
      tier,
      n_completed: 0,
      n_wins: 0,
      win_rate: null,
      avg_return: null,
      gate_pass: false,
      gate_reason: 'NO_DB',
      samples_needed: PROOF_MIN_N,
    };
  }

  const hitCol = horizon === 't1' ? 'hit_t1' : 'hit_t5';
  const retCol = horizon === 't1' ? 'return_t1' : 'return_t5';
  const minFilled = horizon === 't1' ? 1 : 5;

  const db = new Database(DB_PATH, { readonly: true });
  let row;
  try {
    row = db.prepare(`
      SELECT
        COUNT(*) AS n,
        SUM(CASE WHEN ${hitCol} = 1 THEN 1 ELSE 0 END) AS wins,
        AVG(${retCol}) AS avg_ret
      FROM recommendation_outcomes
      WHERE outcome_filled >= ?
        AND ${hitCol} IS NOT NULL
        AND conviction_tier = ?
    `).get(minFilled, tier);
  } finally {
    db.close();
  }

  const n = row?.n ?? 0;
  const wins = row?.wins ?? 0;
  const wr = n > 0 ? (wins / n) * 100 : null;
  const avgRet = row?.avg_ret ?? null;

  let gate_pass = false;
  let gate_reason = 'INSUFFICIENT_SAMPLES';
  if (n >= PROOF_MIN_N) {
    if (wr >= PROOF_MIN_WR) {
      gate_pass = true;
      gate_reason = 'PASS';
    } else {
      gate_reason = 'WR_BELOW_THRESHOLD';
    }
  }

  return {
    tier,
    horizon,
    n_completed: n,
    n_wins: wins,
    win_rate: wr != null ? Math.round(wr * 10) / 10 : null,
    avg_return: avgRet != null ? Math.round(avgRet * 10000) / 100 : null,
    gate_pass,
    gate_reason,
    samples_needed: Math.max(0, PROOF_MIN_N - n),
    target_wr: PROOF_MIN_WR,
  };
}

/** Persist snapshot for handoff / ops digest. */
export function writeProofLoopSnapshot() {
  const metrics = getProofLoopMetrics();
  const payload = { at: new Date().toISOString(), ...metrics };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/proof_loop_last.json'), JSON.stringify(payload, null, 2));
  return payload;
}

export function formatProofLoopLine(metrics) {
  if (!metrics || metrics.n_completed === 0) {
    return `Proof loop: 0/${PROOF_MIN_N} ULTRA samples`;
  }
  const wr = metrics.win_rate != null ? `${metrics.win_rate}%` : '—';
  const icon = metrics.gate_pass ? '✅' : metrics.samples_needed > 0 ? '⏳' : '⚠️';
  return `${icon} Proof loop: ${metrics.n_completed}/${PROOF_MIN_N} ULTRA | WR5 ${wr} (need ≥${PROOF_MIN_WR}%)`;
}
