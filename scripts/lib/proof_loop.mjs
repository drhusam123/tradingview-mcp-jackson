/**
 * P6 Proof Loop — live outcome metrics for client beta gate.
 * Gate: ULTRA_CONVICTION WinRate ≥ 60% over ≥ 30 completed signals (5d horizon).
 *
 * safetyFiltered=true counts only signals that pass current delivery safety rules
 * (counterfactual replay). Raw historical ULTRA tier includes pre-filter losses.
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { DB_PATH } from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluateSignalAtDate } from './egx_safety_check.mjs';

export const PROOF_MIN_N = 30;
export const PROOF_MIN_WR = 60;

function buildProofResult({
  tier, horizon, deliveredOnly, safetyFiltered, n, wins, avgRet,
}) {
  const wr = n > 0 ? (wins / n) * 100 : null;
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
    delivered_only: deliveredOnly,
    safety_filtered: safetyFiltered,
    n_completed: n,
    n_wins: wins,
    win_rate: wr != null ? Math.round(wr * 10) / 10 : null,
    avg_return: avgRet != null ? Math.round(avgRet * 10) / 10 : null,
    gate_pass,
    gate_reason,
    samples_needed: Math.max(0, PROOF_MIN_N - n),
    target_wr: PROOF_MIN_WR,
  };
}

function getSafetyFilteredProofMetrics({ tier = 'ULTRA_CONVICTION', horizon = 't5' } = {}) {
  if (!existsSync(DB_PATH)) {
    return buildProofResult({
      tier, horizon, deliveredOnly: false, safetyFiltered: true,
      n: 0, wins: 0, avgRet: null,
    });
  }

  const hitCol = horizon === 't1' ? 'hit_t1' : 'hit_t5';
  const retCol = horizon === 't1' ? 'return_t1' : 'return_t5';
  const minFilled = horizon === 't1' ? 1 : 5;

  const db = new Database(DB_PATH, { readonly: true });
  let rows;
  try {
    rows = db.prepare(`
      SELECT symbol, signal_date, ${hitCol} AS hit, ${retCol} AS ret
      FROM recommendation_outcomes
      WHERE conviction_tier = ?
        AND outcome_filled >= ?
        AND ${hitCol} IS NOT NULL
      ORDER BY signal_date DESC
    `).all(tier, minFilled);
  } finally {
    db.close();
  }

  const kept = [];
  for (const row of rows) {
    const ev = evaluateSignalAtDate(row.symbol, row.signal_date, {
      historical: true,
      counterfactual: true,
    });
    if (ev.decision !== 'BLOCKED') kept.push(row);
  }

  const n = kept.length;
  const wins = kept.filter(r => r.hit === 1).length;
  const rets = kept.map(r => r.ret).filter(v => v != null);
  const avgRet = rets.length ? rets.reduce((s, x) => s + x, 0) / rets.length : null;

  return buildProofResult({
    tier, horizon, deliveredOnly: false, safetyFiltered: true,
    n, wins, avgRet,
  });
}

function getDeliveredSafetyFilteredMetrics({ horizon = 't5' } = {}) {
  if (!existsSync(DB_PATH)) {
    return buildProofResult({
      tier: 'DELIVERED_COHORT', horizon, deliveredOnly: true, safetyFiltered: true,
      n: 0, wins: 0, avgRet: null,
    });
  }

  const hitCol = horizon === 't1' ? 'hit_t1' : 'hit_t5';
  const retCol = horizon === 't1' ? 'return_t1' : 'return_t5';
  const minFilled = horizon === 't1' ? 1 : 5;

  const db = new Database(DB_PATH, { readonly: true });
  let rows;
  try {
    rows = db.prepare(`
      SELECT symbol, signal_date, ${hitCol} AS hit, ${retCol} AS ret
      FROM recommendation_outcomes
      WHERE COALESCE(client_delivered, 0) = 1
        AND outcome_filled >= ?
        AND ${hitCol} IS NOT NULL
      ORDER BY signal_date DESC
    `).all(minFilled);
  } finally {
    db.close();
  }

  const kept = [];
  for (const row of rows) {
    const ev = evaluateSignalAtDate(row.symbol, row.signal_date, {
      historical: true,
      counterfactual: true,
    });
    if (ev.decision !== 'BLOCKED') kept.push(row);
  }

  const n = kept.length;
  const wins = kept.filter(r => r.hit === 1).length;
  const rets = kept.map(r => r.ret).filter(v => v != null);
  const avgRet = rets.length ? rets.reduce((s, x) => s + x, 0) / rets.length : null;

  return buildProofResult({
    tier: 'DELIVERED_COHORT',
    horizon,
    deliveredOnly: true,
    safetyFiltered: true,
    n,
    wins,
    avgRet,
  });
}

export function getProofLoopMetrics({
  tier = 'ULTRA_CONVICTION',
  horizon = 't5',
  deliveredOnly = false,
  safetyFiltered = false,
  allDeliveredTiers = false,
} = {}) {
  if (deliveredOnly && safetyFiltered) {
    return getDeliveredSafetyFilteredMetrics({ horizon });
  }
  if (safetyFiltered) {
    return getSafetyFilteredProofMetrics({ tier, horizon });
  }
  if (!existsSync(DB_PATH)) {
    return buildProofResult({
      tier, horizon, deliveredOnly, safetyFiltered: false,
      n: 0, wins: 0, avgRet: null,
    });
  }

  const hitCol = horizon === 't1' ? 'hit_t1' : 'hit_t5';
  const retCol = horizon === 't1' ? 'return_t1' : 'return_t5';
  const minFilled = horizon === 't1' ? 1 : 5;

  const db = new Database(DB_PATH, { readonly: true });
  let row;
  try {
    const deliveredClause = deliveredOnly
      ? 'AND COALESCE(client_delivered, 0) = 1'
      : '';
    const tierClause = deliveredOnly && allDeliveredTiers
      ? ''
      : 'AND conviction_tier = ?';
    const args = [minFilled];
    if (!(deliveredOnly && allDeliveredTiers)) args.push(tier);
    row = db.prepare(`
      SELECT
        COUNT(*) AS n,
        SUM(CASE WHEN ${hitCol} = 1 THEN 1 ELSE 0 END) AS wins,
        AVG(${retCol}) AS avg_ret
      FROM recommendation_outcomes
      WHERE outcome_filled >= ?
        AND ${hitCol} IS NOT NULL
        ${tierClause}
        ${deliveredClause}
    `).get(...args);
  } finally {
    db.close();
  }

  return buildProofResult({
    tier,
    horizon,
    deliveredOnly,
    safetyFiltered: false,
    n: row?.n ?? 0,
    wins: row?.wins ?? 0,
    avgRet: row?.avg_ret ?? null,
  });
}

/** Persist snapshot for handoff / ops digest. */
export function writeProofLoopSnapshot() {
  const raw = getProofLoopMetrics();
  const filtered = getProofLoopMetrics({ safetyFiltered: true });
  const payload = {
    at: new Date().toISOString(),
    ...filtered,
    raw_track: {
      n_completed: raw.n_completed,
      win_rate: raw.win_rate,
      gate_pass: raw.gate_pass,
      gate_reason: raw.gate_reason,
    },
  };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/proof_loop_last.json'), JSON.stringify(payload, null, 2));
  return payload;
}

export function formatProofLoopLine(metrics, { label = 'Proof loop' } = {}) {
  if (!metrics || metrics.n_completed === 0) {
    const tag = metrics?.safety_filtered ? ' (safety-filtered)' : '';
    return `${label}: 0/${PROOF_MIN_N} ULTRA samples${tag}`;
  }
  const wr = metrics.win_rate != null ? `${metrics.win_rate}%` : '—';
  const icon = metrics.gate_pass ? '✅' : metrics.samples_needed > 0 ? '⏳' : '⚠️';
  const tag = metrics.safety_filtered ? ' safety-filtered' : '';
  return `${icon} ${label}: ${metrics.n_completed}/${PROOF_MIN_N} ULTRA${tag} | WR5 ${wr} (need ≥${PROOF_MIN_WR}%)`;
}
