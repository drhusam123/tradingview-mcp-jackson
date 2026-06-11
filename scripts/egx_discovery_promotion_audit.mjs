#!/usr/bin/env node
/** Audit high-opp symbols blocked from promotion — closes PROMOTION_GAP loop. */
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { writeFileSync, mkdirSync } from 'fs';
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';
import { latestReadySignalDate, DB_PATH } from './lib/delivery_audit.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';
import { PROJECT_ROOT } from './lib/load_env.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const signalDate = latestReadySignalDate();

if (!signalDate || !existsSync(DB_PATH)) {
  console.error(JSON.stringify({ ok: false, error: 'NO_DATA' }));
  process.exit(1);
}

const ctx = buildDiscoveryParams({ signalDate });
const promoParams = JSON.stringify({ date: signalDate, ...ctx.params, dry_run: true });

let promo;
try {
  promo = parsePythonJson(execFileSync(PYTHON3, [
    join(ROOT, 'scripts/python/client_signal_promotion.py'),
    promoParams,
  ], { cwd: ROOT, encoding: 'utf8', timeout: 120000 }));
} catch (e) {
  promo = { error: e.message, promoted: 0 };
}

const db = new Database(DB_PATH, { readonly: true });
const highOpp = db.prepare(`
  SELECT o.symbol, o.opportunity_score, o.stage, o.structure_score,
         fs.score AS ues, fs.veto_reason, fs.actionable
  FROM opportunity_score_v2 o
  JOIN final_signals fs ON fs.symbol=o.symbol AND fs.trade_date=o.trade_date
  WHERE o.trade_date=? AND o.opportunity_score>=75
  ORDER BY o.opportunity_score DESC LIMIT 20
`).all(signalDate);
db.close();

const missed = highOpp.filter(r => !r.actionable);
const missedActionableCandidates = missed.filter(r => r.stage === 'ACTIONABLE_CANDIDATE');
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  promoted: promo.promoted ?? 0,
  n_skipped: promo.n_skipped ?? null,
  skipped_sample: promo.skipped_sample ?? [],
  promotion_tuning: promo.promotion_tuning ?? null,
  high_opp_missed: missed.map(r => ({
    symbol: r.symbol,
    opp: r.opportunity_score,
    stage: r.stage,
    structure: r.structure_score,
    ues: r.ues,
    veto: r.veto_reason,
    actionable: Boolean(r.actionable),
  })),
  missed_actionable_candidates: missedActionableCandidates.length,
  ok: (promo.promoted ?? 0) > 0 || missedActionableCandidates.length === 0,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_promotion_audit_last.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 1);
