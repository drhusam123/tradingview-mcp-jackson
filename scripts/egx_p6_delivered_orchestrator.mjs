#!/usr/bin/env node
/**
 * P6 delivered orchestrator — sync audit→outcomes, compute WR gap, plan live sessions.
 * Usage: node scripts/egx_p6_delivered_orchestrator.mjs [--json]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { cairoDateParts, nextTradingDay } from './lib/egx_calendar.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const NODE = process.execPath;

const sync = syncDeliveredOutcomes({ lookbackDays: 180 });
const proofAll = getProofLoopMetrics({ deliveredOnly: false });
const proofDel = getProofLoopMetrics({ deliveredOnly: true });

// Wins needed to reach 60% WR if we only add winning sessions
const wins = proofAll.n_wins ?? 0;
const n = proofAll.n_completed ?? 0;
let wins_needed = 0;
if (n >= PROOF_MIN_N && (proofAll.win_rate ?? 0) < PROOF_MIN_WR) {
  for (let k = 1; k <= 50; k++) {
    if ((wins + k) / (n + k) >= PROOF_MIN_WR / 100) {
      wins_needed = k;
      break;
    }
  }
}

let sessionDate = cairoDateParts().date;
const sessionDates = [];
const nSessions = Math.max(4, wins_needed || 4);
for (let i = 0; i < nSessions; i++) {
  const nxt = nextTradingDay(sessionDate);
  sessionDate = nxt.next_trading_day;
  if (sessionDate) sessionDates.push(sessionDate);
}

let recovery = { pending: 0 };
try {
  const out = execSync(`"${NODE}" scripts/egx_notify_recovery.mjs`, {
    cwd: PROJECT_ROOT,
    encoding: 'utf8',
    timeout: 60_000,
  });
  const m = out.match(/Pending: (\d+)/);
  recovery.pending = m ? Number(m[1]) : 0;
} catch { /* */ }

const plan = {
  at: new Date().toISOString(),
  gate: {
    n_completed: proofAll.n_completed,
    n_wins: proofAll.n_wins,
    win_rate: proofAll.win_rate,
    gate_pass: proofAll.gate_pass,
    gate_reason: proofAll.gate_reason,
    samples_needed: proofAll.samples_needed,
    wins_needed_for_60pct: wins_needed,
    blocker: proofAll.gate_pass
      ? null
      : proofAll.samples_needed > 0
        ? 'INSUFFICIENT_SAMPLES'
        : 'WR_BELOW_THRESHOLD',
  },
  delivered: {
    n_completed: proofDel.n_completed,
    win_rate: proofDel.win_rate,
    sync,
  },
  recovery,
  next_live_sessions: sessionDates.map((d, i) => ({
    session: i + 1,
    date: d,
    checklist: [
      'npm run egx:prod:prepare-send',
      'npm run egx:telegram:cron (no --dry-run)',
      'npm run egx:post:session',
    ],
  })),
  commands: {
    prepare: 'npm run egx:prod:prepare-send',
    send: 'npm run egx:telegram:cron',
    post: 'npm run egx:post:session',
    recovery: 'EGX_AUTO_BACKFILL=1 npm run egx:notify:recovery -- --send',
  },
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/p6_delivered_orchestrator_last.json'), JSON.stringify(plan, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(plan, null, 2));
} else {
  console.log('\n═══ P6 Delivered Orchestrator ═══\n');
  console.log(`  Gate: ${proofAll.n_completed}/${PROOF_MIN_N} ULTRA @ ${proofAll.win_rate ?? '—'}% (${proofAll.gate_reason})`);
  if (wins_needed) console.log(`  Wins needed (all-win streak): ${wins_needed}`);
  console.log(`  Delivered track: ${proofDel.n_completed} synced (${sync.rows_updated} rows updated)`);
  console.log(`  Recovery pending: ${recovery.pending}`);
  console.log(`  Next sessions: ${sessionDates.slice(0, 4).join(', ')}`);
  console.log('\n  Saved: data/p6_delivered_orchestrator_last.json\n');
}

process.exit(0);
