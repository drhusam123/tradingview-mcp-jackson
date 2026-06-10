/**
 * Counterfactual safety — replay current rules on historical ULTRA outcomes.
 * Measures projected WR if today's delivery filters had been active.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';
import { evaluateSignalAtDate, loadEgxRules } from './egx_safety_check.mjs';
import { PROOF_MIN_N, PROOF_MIN_WR } from './proof_loop.mjs';

const TIER = 'ULTRA_CONVICTION';

export function runCounterfactualSafety({ tier = TIER, horizon = 't5' } = {}) {
  if (!existsSync(DB_PATH)) {
    return { error: 'NO_DB', tier, n_historical: 0 };
  }

  const hitCol = horizon === 't1' ? 'hit_t1' : 'hit_t5';
  const minFilled = horizon === 't1' ? 1 : 5;

  const db = new Database(DB_PATH, { readonly: true });
  const rows = db.prepare(`
    SELECT symbol, signal_date, ${hitCol} AS hit, return_t5, return_t1,
           behavioral_class, conviction_tier
    FROM recommendation_outcomes
    WHERE conviction_tier = ?
      AND outcome_filled >= ?
      AND ${hitCol} IS NOT NULL
    ORDER BY signal_date DESC
  `).all(tier, minFilled);
  db.close();

  const rules = loadEgxRules();
  const replay = [];

  for (const row of rows) {
    const ev = evaluateSignalAtDate(row.symbol, row.signal_date, { historical: true, counterfactual: true });
    const wouldBlock = ev.decision === 'BLOCKED';
    replay.push({
      symbol: row.symbol,
      signal_date: row.signal_date,
      hit: row.hit,
      return_t5: row.return_t5,
      behavioral_class: row.behavioral_class || ev.behavioral_class,
      would_block_now: wouldBlock,
      block_reasons: ev.failed_conditions,
    });
  }

  const n = replay.length;
  const wins = replay.filter(r => r.hit === 1);
  const losses = replay.filter(r => r.hit === 0);
  const blocked = replay.filter(r => r.would_block_now);
  const blockedWins = blocked.filter(r => r.hit === 1);
  const blockedLosses = blocked.filter(r => r.hit === 0);

  const kept = replay.filter(r => !r.would_block_now);
  const keptWins = kept.filter(r => r.hit === 1).length;
  const keptN = kept.length;
  const actualWr = n > 0 ? Math.round((wins.length / n) * 1000) / 10 : null;
  const projectedWr = keptN > 0 ? Math.round((keptWins / keptN) * 1000) / 10 : null;

  const blockReasonCounts = {};
  for (const r of blocked) {
    for (const reason of r.block_reasons) {
      blockReasonCounts[reason] = (blockReasonCounts[reason] || 0) + 1;
    }
  }

  return {
    tier,
    rules_version: rules.description || 'egx_rules.json',
    n_historical: n,
    actual_wr: actualWr,
    projected_wr: projectedWr,
    wr_delta: projectedWr != null && actualWr != null
      ? Math.round((projectedWr - actualWr) * 10) / 10
      : null,
    would_block: blocked.length,
    would_block_losses: blockedLosses.length,
    would_block_wins: blockedWins.length,
    kept_n: keptN,
    kept_wins: keptWins,
    p6_gate_pass_projected: keptN >= PROOF_MIN_N && (projectedWr ?? 0) >= PROOF_MIN_WR,
    block_reason_counts: blockReasonCounts,
    loss_symbols_still_passing: losses
      .filter(r => !r.would_block_now)
      .map(r => ({ symbol: r.symbol, date: r.signal_date, class: r.behavioral_class })),
    sample_blocked_losses: blockedLosses.slice(0, 8).map(r => ({
      symbol: r.symbol,
      date: r.signal_date,
      reasons: r.block_reasons,
    })),
  };
}
