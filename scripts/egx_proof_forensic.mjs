#!/usr/bin/env node
/**
 * P6 Proof Loop forensic — ULTRA_CONVICTION outcome breakdown.
 * Usage: node scripts/egx_proof_forensic.mjs [--json]
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { DB_PATH } from './lib/delivery_audit.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const TIER = 'ULTRA_CONVICTION';

function analyze() {
  const base = getProofLoopMetrics({ tier: TIER });
  if (!existsSync(DB_PATH)) {
    return { ...base, error: 'NO_DB', losses: [], wins: [], by_class: {} };
  }

  const db = new Database(DB_PATH, { readonly: true });
  const rows = db.prepare(`
    SELECT symbol, signal_date, entry_price, close_t5, return_t5, return_t1,
           hit_t5, hit_stop, behavioral_class, quality_gate_passed, ues, ml_score
    FROM recommendation_outcomes
    WHERE conviction_tier = ?
      AND outcome_filled >= 5
      AND hit_t5 IS NOT NULL
    ORDER BY signal_date DESC
  `).all(TIER);

  const wins = rows.filter(r => r.hit_t5 === 1);
  const losses = rows.filter(r => r.hit_t5 === 0);

  const byClass = {};
  for (const r of rows) {
    const cls = r.behavioral_class || 'UNKNOWN';
    if (!byClass[cls]) byClass[cls] = { n: 0, wins: 0, losses: 0 };
    byClass[cls].n += 1;
    if (r.hit_t5 === 1) byClass[cls].wins += 1;
    else byClass[cls].losses += 1;
  }

  const avg = arr => arr.length
    ? arr.reduce((s, x) => s + x, 0) / arr.length
    : null;

  const winRets = wins.map(r => r.return_t5).filter(v => v != null);
  const lossRets = losses.map(r => r.return_t5).filter(v => v != null);

  db.close();

  return {
    ...base,
    tier: TIER,
    n_total: rows.length,
    n_wins: wins.length,
    n_losses: losses.length,
    avg_win_ret5: avg(winRets) != null ? Math.round(avg(winRets) * 10) / 10 : null,
    avg_loss_ret5: avg(lossRets) != null ? Math.round(avg(lossRets) * 10) / 10 : null,
    explosive_loss_share: losses.filter(r => r.behavioral_class === 'EXPLOSIVE').length,
    by_class: byClass,
    recent_losses: losses.slice(0, 10).map(r => ({
      symbol: r.symbol,
      date: r.signal_date,
      ret5: r.return_t5,
      hit_stop: r.hit_stop,
      class: r.behavioral_class,
      entry: r.entry_price,
      close5: r.close_t5,
    })),
    recent_wins: wins.slice(0, 5).map(r => ({
      symbol: r.symbol,
      date: r.signal_date,
      ret5: r.return_t5,
      class: r.behavioral_class,
    })),
    insight: buildInsight(losses, byClass, base),
  };
}

function buildInsight(losses, byClass, base) {
  const lines = [];
  if (base.samples_needed > 0) {
    lines.push(`Need ${base.samples_needed} more ULTRA samples before P6 gate (${PROOF_MIN_N} total, WR≥${PROOF_MIN_WR}%).`);
  }
  const expLoss = losses.filter(r => r.behavioral_class === 'EXPLOSIVE').length;
  if (expLoss >= 5) {
    lines.push(`${expLoss}/${losses.length} losses are EXPLOSIVE behavioral_class — review TRADING_LESSONS vol/session filters.`);
  }
  const volatileLoss = (byClass.VOLATILE?.losses ?? 0);
  if (volatileLoss >= 3) {
    lines.push(`${volatileLoss} VOLATILE-class ULTRA losses — structural SL may be too tight or entries extended.`);
  }
  const noQG = losses.filter(r => !r.quality_gate_passed).length;
  if (noQG === losses.length && losses.length > 0) {
    lines.push('All ULTRA losses predate quality_gate_passed column — historical seed rows, not live pipeline failures.');
  }
  return lines;
}

const report = analyze();
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/proof_forensic_last.json'), JSON.stringify({ at: new Date().toISOString(), ...report }, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

console.log('\n═══ P6 Proof Forensic (ULTRA_CONVICTION) ═══\n');
console.log(`  Samples : ${report.n_completed}/${PROOF_MIN_N} | WR5 ${report.win_rate ?? '—'}% (gate ≥${PROOF_MIN_WR}%)`);
console.log(`  Wins    : ${report.n_wins}  avg +${report.avg_win_ret5 ?? '—'}%`);
console.log(`  Losses  : ${report.n_losses}  avg ${report.avg_loss_ret5 ?? '—'}%`);
console.log(`  Gate    : ${report.gate_pass ? '✅ PASS' : report.gate_reason}\n`);

if (Object.keys(report.by_class).length) {
  console.log('  By behavioral_class:');
  for (const [cls, v] of Object.entries(report.by_class)) {
    const wr = v.n ? Math.round((v.wins / v.n) * 1000) / 10 : 0;
    console.log(`    ${cls.padEnd(12)} ${v.wins}W/${v.losses}L  (${wr}% WR)`);
  }
  console.log('');
}

if (report.recent_losses?.length) {
  console.log('  Recent losses:');
  for (const r of report.recent_losses) {
    const stop = r.hit_stop ? ' SL✓' : '';
    console.log(`    ${r.symbol.padEnd(6)} ${r.date}  ${r.ret5 >= 0 ? '+' : ''}${r.ret5?.toFixed?.(1) ?? r.ret5}%  ${r.class || ''}${stop}`);
  }
  console.log('');
}

if (report.insight?.length) {
  console.log('  Insights:');
  report.insight.forEach(l => console.log(`    • ${l}`));
  console.log('');
}
